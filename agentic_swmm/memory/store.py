"""One database for project memory (part 2 of the memory simplification, 2026-09-06).

``memory/store/memory.sqlite`` already held the sessions (``session_db``
owns the file and its schema). It now also holds the project-memory
tables ``failures``, ``parametric``, ``calibration``, ``negative_lessons``
and ``runs``. The JSONL ledgers next to it stay the append-only truth;
the tables are the query surface:

* each ledger table is a lazily synced index of its ledger, keyed by the
  ledger's size and mtime, so readers see every appended row without a
  second write path (``ledger_rows``);
* ``runs`` is filled by the audit hook from each run's provenance and by
  ``rebuild`` from a walk of ``runs/``;
* ``rebuild`` sets the database aside and rebuilds everything from the
  ledgers and ``runs/``; deleting the file loses nothing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agentic_swmm.memory import session_db
from agentic_swmm.memory.jsonl_store import dump_line, iter_rows

DB_FILENAME = "memory.sqlite"

#: ledger file name -> table name
LEDGER_TABLES: dict[str, str] = {
    "run_failures.jsonl": "failures",
    "parametric_memory.jsonl": "parametric",
    "calibration_memory.jsonl": "calibration",
    "negative_lessons.jsonl": "negative_lessons",
}

#: columns copied out of the raw row so the table can be queried without
#: parsing JSON; everything else stays in ``raw``.
_INDEXED: dict[str, tuple[str, ...]] = {
    "failures": ("run_id", "tool", "failure_class", "summary", "recorded_at", "pattern", "fix"),
    "parametric": ("run_id", "case_name", "calibration_status", "recorded_utc"),
    "calibration": ("run_id", "case_name", "use_case", "algorithm", "objective_name", "created_at"),
    "negative_lessons": ("run_id", "case_name", "lesson_type", "recorded_at"),
}


def db_path_for(store_or_ledger: Path) -> Path:
    """The database next to a ledger, or inside a store directory."""
    path = Path(store_or_ledger)
    directory = path if path.suffix == "" and path.name != DB_FILENAME else path.parent
    return directory / DB_FILENAME


def table_for(ledger_path: Path) -> str | None:
    return LEDGER_TABLES.get(Path(ledger_path).name)


def _fingerprint(row: Any) -> str:
    return hashlib.sha1(dump_line(row).encode("utf-8")).hexdigest()


def _ledger_state(ledger: Path) -> tuple[int, int]:
    stat = ledger.stat()
    return int(stat.st_size), int(stat.st_mtime_ns)


def sync_ledger(db_path: Path, ledger: Path) -> int:
    """Import the rows of ``ledger`` its table does not have yet.

    Idempotent: rows are keyed by a fingerprint of their canonical JSON
    line, and the import is skipped when the ledger's size and mtime
    match the last import. Returns the number of rows added.
    """
    ledger = Path(ledger)
    table = table_for(ledger)
    if table is None or not ledger.is_file():
        return 0
    size, mtime = _ledger_state(ledger)
    with session_db.connect(db_path) as conn:
        state = conn.execute(
            "SELECT size, mtime_ns FROM ledger_state WHERE ledger = ?", (ledger.name,)
        ).fetchone()
        if state is not None and int(state[0]) == size and int(state[1]) == mtime:
            return 0
        added = _insert_rows(conn, table, iter_rows(ledger))
        conn.execute(
            "INSERT OR REPLACE INTO ledger_state(ledger, size, mtime_ns, synced_utc) VALUES (?, ?, ?, ?)",
            (ledger.name, size, mtime, _utcnow()),
        )
        conn.commit()
    return added


def _insert_rows(conn: sqlite3.Connection, table: str, rows: Iterable[Any]) -> int:
    columns = _INDEXED[table]
    placeholders = ", ".join("?" for _ in columns)
    sql = (
        f"INSERT OR IGNORE INTO {table} (fingerprint, {', '.join(columns)}, raw) "
        f"VALUES (?, {placeholders}, ?)"
    )
    added = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        values = [_fingerprint(row)]
        values.extend(_scalar(row.get(column)) for column in columns)
        values.append(dump_line(row))
        cursor = conn.execute(sql, values)
        added += int(cursor.rowcount > 0)
    return added


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float)):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def ledger_rows(ledger: Path, *, case_name: str | None = None) -> list[dict[str, Any]]:
    """Every row of ``ledger``, read through its table (synced first).

    A missing ledger yields ``[]`` without creating the database, so a
    fresh project pays nothing until it records something.
    """
    ledger = Path(ledger)
    table = table_for(ledger)
    if table is None or not ledger.is_file():
        return []
    db_path = db_path_for(ledger)
    sync_ledger(db_path, ledger)
    where, params = "", []
    if case_name is not None and "case_name" in _INDEXED[table]:
        where, params = " WHERE case_name = ?", [case_name]
    with session_db.connect(db_path) as conn:
        cursor = conn.execute(f"SELECT raw FROM {table}{where} ORDER BY id", params)
        out: list[dict[str, Any]] = []
        for (raw,) in cursor:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
        return out


def table_counts(db_path: Path) -> dict[str, int]:
    """Row counts of the project-memory tables (and sessions)."""
    db_path = Path(db_path)
    if not db_path.is_file():
        return {}
    counts: dict[str, int] = {}
    with session_db.connect(db_path) as conn:
        for table in ("sessions", "runs", *LEDGER_TABLES.values()):
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return counts


# ---------------------------------------------------------------------------
# runs: one row per audited run, from its provenance
# ---------------------------------------------------------------------------


def run_row_from_dir(run_dir: Path) -> dict[str, Any] | None:
    """Build the ``runs`` row for ``run_dir`` from its audit provenance."""
    run_dir = Path(run_dir)
    provenance: dict[str, Any] = {}
    for relative in ("09_audit/experiment_provenance.json", "experiment_provenance.json"):
        candidate = run_dir / relative
        if candidate.is_file():
            try:
                loaded = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            provenance = loaded if isinstance(loaded, dict) else {}
            break
    if not provenance:
        return None
    metrics = provenance.get("metrics") if isinstance(provenance.get("metrics"), dict) else {}
    qa = provenance.get("qa") if isinstance(provenance.get("qa"), dict) else {}
    diagnostics = (
        provenance.get("model_diagnostics") if isinstance(provenance.get("model_diagnostics"), dict) else {}
    )
    summary: dict[str, Any] = {}
    summary_path = run_dir / "memory_summary.json"
    if summary_path.is_file():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            summary = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            summary = {}
    run_id = str(provenance.get("run_id") or run_dir.name)
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "case_name": provenance.get("case_name") or summary.get("case_name"),
        "project": summary.get("project_key"),
        "workflow_mode": provenance.get("workflow_mode") or summary.get("workflow_mode"),
        "status": provenance.get("status"),
        "qa_status": qa.get("status") or summary.get("qa_status"),
        "diagnostics_status": diagnostics.get("status"),
        "swmm_return_code": _scalar(metrics.get("swmm_return_code", summary.get("swmm_return_code"))),
        "peak_flow": _scalar(metrics.get("peak_flow")),
        "continuity": _scalar(metrics.get("continuity_error")),
        "failure_patterns": _scalar(summary.get("failure_patterns")),
        "generated_at_utc": provenance.get("generated_at_utc"),
    }


def upsert_run(db_path: Path, row: dict[str, Any]) -> None:
    columns = list(row)
    placeholders = ", ".join("?" for _ in columns)
    with session_db.connect(db_path) as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO runs ({', '.join(columns)}) VALUES ({placeholders})",
            [row[column] for column in columns],
        )
        conn.commit()


def record_run(store_dir: Path, run_dir: Path) -> dict[str, Any] | None:
    """Audit-hook entry point: upsert the run's row. Returns the row or None."""
    row = run_row_from_dir(run_dir)
    if row is None:
        return None
    upsert_run(db_path_for(Path(store_dir)), row)
    return row


def runs_for_case(db_path: Path, case_name: str) -> list[dict[str, Any]]:
    db_path = Path(db_path)
    if not db_path.is_file():
        return []
    with session_db.connect(db_path) as conn:
        cursor = conn.execute(
            "SELECT * FROM runs WHERE case_name = ? ORDER BY generated_at_utc", (case_name,)
        )
        return [dict(r) for r in cursor.fetchall()]


# ---------------------------------------------------------------------------
# rebuild
# ---------------------------------------------------------------------------


def rebuild(store_dir: Path, runs_dir: Path | None = None) -> dict[str, Any]:
    """Set the database aside and rebuild it from the ledgers and ``runs/``.

    Returns ``{"db_path", "backup", "ledgers": {table: rows}, "runs": n,
    "sessions": {...}}``. The previous file is kept as
    ``memory.sqlite.before-rebuild-<utc>`` next to the new one.
    """
    store_dir = Path(store_dir)
    db_path = db_path_for(store_dir)
    result: dict[str, Any] = {"db_path": str(db_path), "backup": None, "ledgers": {}, "runs": 0, "sessions": {}}
    if db_path.exists():
        backup = db_path.with_name(f"{DB_FILENAME}.before-rebuild-{_utcnow().replace(':', '')}")
        db_path.replace(backup)
        result["backup"] = str(backup)
    session_db.clear_integrity_cache()
    session_db.initialize(db_path)
    for ledger_name, table in LEDGER_TABLES.items():
        ledger = store_dir / ledger_name
        result["ledgers"][table] = sync_ledger(db_path, ledger) if ledger.is_file() else 0
    if runs_dir is not None and Path(runs_dir).is_dir():
        from agentic_swmm.memory.session_sync import sync_session_to_db

        rebuilt = messages = failures = 0
        for state in sorted(Path(runs_dir).rglob("session_state.json")):
            try:
                sync = sync_session_to_db(state.parent, db_path=db_path)
            except Exception:  # noqa: BLE001 - one bad session never stops the rebuild
                failures += 1
                continue
            if sync.get("ok"):
                rebuilt += 1
                messages += int(sync.get("messages") or 0)
            else:
                failures += 1
        result["sessions"] = {"rebuilt": rebuilt, "messages": messages, "failures": failures}
        seen: set[str] = set()
        for provenance in sorted(Path(runs_dir).rglob("experiment_provenance.json")):
            run_dir = provenance.parent.parent if provenance.parent.name == "09_audit" else provenance.parent
            key = str(run_dir)
            if key in seen:
                continue
            seen.add(key)
            row = run_row_from_dir(run_dir)
            if row is not None:
                upsert_run(db_path, row)
                result["runs"] += 1
    session_db.clear_integrity_cache()
    return result


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "DB_FILENAME",
    "LEDGER_TABLES",
    "db_path_for",
    "ledger_rows",
    "rebuild",
    "record_run",
    "run_row_from_dir",
    "runs_for_case",
    "sync_ledger",
    "table_counts",
    "table_for",
    "upsert_run",
]
