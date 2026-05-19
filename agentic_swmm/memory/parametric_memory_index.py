"""SQLite read-acceleration sidecar for ``parametric_memory.jsonl``.

PRD-06 Phase A §4.1 calls for SQLite-backed indexed lookup once the
parametric store exceeds ~1k rows. The store itself stays JSONL — that
file is the canonical source of truth, edited by append-only writes
from the audit hook and calibration batches. SQLite is a *derived*
index, rebuilt from the JSONL on demand. Deleting the sidecar is safe;
it rebuilds on the next read.

Design notes
------------
- One sidecar per JSONL store, named ``<jsonl>.sqlite3``. Tests can
  create multiple stores under tempdirs without collisions.
- Indices on ``case_name``, ``use_case`` (mirrored from
  ``model_structure`` and ``calibration_status`` blob columns are
  picked up by SQL ``=`` lookups via stored columns. Nested-dotted
  filters (e.g. ``model_structure.routing``) fall back to a JSON
  comparison in SQL — still much faster than scanning the JSONL for
  large stores.
- ``IndexStaleError`` is raised when the sidecar exists but the JSONL
  has been written since. The recall path catches it and linear-scans
  the JSONL — the only always-correct fallback.

Why not switch the writer to SQLite outright? JSONL is git-friendly,
streams cleanly under partial writes, and is the file other tools
(diffing tools, dumb shell scripts, code search) understand. Keeping
JSONL canonical preserves those properties.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


#: Once the JSONL store has more than this many rows, ``recall_parametric``
#: switches to the SQLite sidecar for query acceleration.
DEFAULT_INDEX_THRESHOLD = 1000


class IndexStaleError(RuntimeError):
    """Raised by :func:`recall_via_index` when the sidecar is older than
    the JSONL. Callers catch this and fall back to a linear scan."""


def index_path_for(store_path: Path) -> Path:
    """Return the conventional sidecar path for ``store_path``.

    The sidecar lives next to the JSONL with a ``.sqlite3`` suffix.
    Keeping it next to the JSONL means deleting one of the two for any
    reason is recoverable: the JSONL alone can rebuild the sidecar; the
    sidecar alone is meaningless without the JSONL it derives from.
    """
    return Path(store_path).with_suffix(Path(store_path).suffix + ".sqlite3")


def _count_jsonl_rows(store_path: Path) -> int:
    """Count non-empty lines in the JSONL — used by :func:`needs_index`.

    Counting lines is O(file_size) but we only invoke it during a
    recall, which is already O(rows) for the linear path. The cost is
    bounded by what we would otherwise pay.
    """
    count = 0
    try:
        with store_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                if raw.strip():
                    count += 1
    except OSError:
        return 0
    return count


def needs_index(
    store_path: Path, *, threshold_rows: int = DEFAULT_INDEX_THRESHOLD
) -> bool:
    """Return ``True`` if the JSONL warrants a SQLite sidecar build/refresh.

    Two conditions, both must hold:
    1. The JSONL has *more than* ``threshold_rows`` rows. At and below
       the threshold, a linear scan is cheaper than the index build
       cost.
    2. Either the sidecar does not exist, *or* the JSONL is newer than
       the sidecar (mtime comparison).

    A store that has not yet crossed the threshold returns ``False``
    even if a sidecar exists — that lets a user manually pre-build the
    index for a small store without forcing a rebuild on every read.
    """
    store_path = Path(store_path)
    if not store_path.is_file():
        return False

    if _count_jsonl_rows(store_path) <= threshold_rows:
        return False

    sidecar = index_path_for(store_path)
    if not sidecar.is_file():
        return True

    try:
        jsonl_mtime = store_path.stat().st_mtime
        sidecar_mtime = sidecar.stat().st_mtime
    except OSError:
        return True

    return jsonl_mtime > sidecar_mtime


def _create_schema(connection: sqlite3.Connection) -> None:
    """Create the ``parametric_records`` table and its indices.

    Top-level scalar columns: ``run_id``, ``case_name``, ``swmm_version``,
    ``calibration_status``, ``parameter_set_ref``, ``evidence_runs_count``,
    ``recorded_utc``, ``schema_version``.

    JSON-encoded blob columns: ``model_structure``, ``qa_metrics``,
    ``performance_metrics``, ``watershed_classification``.

    Indices on the columns the PRD calls out for filtered lookup.
    """
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS parametric_records (
            row_id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id              TEXT,
            case_name           TEXT,
            swmm_version        TEXT,
            calibration_status  TEXT,
            parameter_set_ref   TEXT,
            evidence_runs_count INTEGER,
            recorded_utc        TEXT,
            schema_version      TEXT,
            model_structure         TEXT,
            qa_metrics              TEXT,
            performance_metrics     TEXT,
            watershed_classification TEXT,
            raw_row             TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_case_name
            ON parametric_records (case_name);
        CREATE INDEX IF NOT EXISTS idx_swmm_version
            ON parametric_records (swmm_version);
        CREATE INDEX IF NOT EXISTS idx_calibration_status
            ON parametric_records (calibration_status);
        CREATE INDEX IF NOT EXISTS idx_recorded_utc
            ON parametric_records (recorded_utc);
        """
    )


def _iter_jsonl_rows(store_path: Path):
    """Yield ``(parsed_row, raw_line)`` tuples; skip torn/empty lines."""
    from agentic_swmm.memory.version_compat import migrate_record

    with store_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            row = migrate_record("parametric_memory", row)
            yield row, stripped


def build_or_refresh_index(store_path: Path) -> Path:
    """(Re)build the SQLite sidecar from the JSONL store.

    Idempotent: the function drops the existing table contents and
    replays every JSONL line. The cost is O(rows) — comparable to the
    linear scan it replaces — but amortized over many subsequent
    indexed reads.

    Returns the sidecar path. Raises ``OSError`` if the JSONL is
    unreadable or the sidecar is unwritable; callers can degrade to a
    linear scan.
    """
    store_path = Path(store_path)
    sidecar = index_path_for(store_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)

    # Drop and rebuild rather than incrementally maintain — the JSONL
    # is append-only but the read-path needs to handle deletes and
    # edits to the canonical file too. A full rebuild matches the
    # cost of a linear scan once and trades it for many fast reads.
    connection = sqlite3.connect(str(sidecar))
    try:
        _create_schema(connection)
        connection.execute("DELETE FROM parametric_records")

        rows_to_insert = []
        for row, raw_line in _iter_jsonl_rows(store_path):
            if not isinstance(row, dict):
                continue
            rows_to_insert.append(
                (
                    row.get("run_id"),
                    row.get("case_name"),
                    row.get("swmm_version"),
                    row.get("calibration_status"),
                    row.get("parameter_set_ref"),
                    row.get("evidence_runs_count"),
                    row.get("recorded_utc"),
                    row.get("schema_version"),
                    json.dumps(row.get("model_structure") or {}, sort_keys=True),
                    json.dumps(row.get("qa_metrics") or {}, sort_keys=True),
                    json.dumps(row.get("performance_metrics") or {}, sort_keys=True),
                    json.dumps(
                        row.get("watershed_classification") or {}, sort_keys=True
                    ),
                    raw_line,
                )
            )

        connection.executemany(
            """
            INSERT INTO parametric_records (
                run_id, case_name, swmm_version, calibration_status,
                parameter_set_ref, evidence_runs_count, recorded_utc,
                schema_version, model_structure, qa_metrics,
                performance_metrics, watershed_classification, raw_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
        connection.commit()
    finally:
        connection.close()

    # Touch the sidecar to advance its mtime past the JSONL's mtime so
    # ``needs_index`` does not immediately schedule another rebuild on
    # tmpfs filesystems with coarse mtime resolution.
    try:
        jsonl_mtime = store_path.stat().st_mtime
        # Bump by a microsecond to clear coarse-mtime-resolution races.
        import os

        os.utime(sidecar, (jsonl_mtime + 0.001, jsonl_mtime + 0.001))
    except OSError:
        pass

    return sidecar


#: Top-level scalar columns that map 1:1 from the dataclass.
_SCALAR_COLUMNS = frozenset(
    {
        "run_id",
        "case_name",
        "swmm_version",
        "calibration_status",
        "parameter_set_ref",
        "evidence_runs_count",
        "recorded_utc",
        "schema_version",
    }
)

#: Top-level dict columns; nested dotted filters land here.
_BLOB_COLUMNS = frozenset(
    {
        "model_structure",
        "qa_metrics",
        "performance_metrics",
        "watershed_classification",
    }
)


def _build_where_clause(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    """Translate the ``recall_parametric`` filter dict into SQL.

    For top-level scalar fields we get a direct ``column = ?`` clause.
    For dotted filters into one of the blob dicts we use SQLite's
    ``json_extract`` — still indexed by the blob column read but at
    least we avoid pulling every row into Python to filter.

    Filters into a column SQLite doesn't know about fall through into a
    ``WHERE FALSE`` clause; the recall path catches the empty result
    and falls back to the linear scan (which can match e.g. unknown
    extras fields).
    """
    if not filters:
        return "", []

    clauses: list[str] = []
    params: list[Any] = []
    for key, expected in filters.items():
        if "." in key:
            top, _, rest = key.partition(".")
            if top in _BLOB_COLUMNS:
                # SQLite's json_extract returns the value as a JSON-typed
                # scalar; comparing with a Python value goes through the
                # type adapter. For dict/list expected values we fall
                # back to JSON string comparison.
                if isinstance(expected, (dict, list)):
                    clauses.append(
                        f"json_extract({top}, '$.{rest}') = ?"
                    )
                    params.append(json.dumps(expected, sort_keys=True))
                else:
                    clauses.append(
                        f"json_extract({top}, '$.{rest}') = ?"
                    )
                    params.append(expected)
                continue
            # Dotted into an unknown blob — force empty result so the
            # caller falls back to the linear scan.
            return " WHERE 1=0", []

        if key in _SCALAR_COLUMNS:
            clauses.append(f"{key} = ?")
            params.append(expected)
            continue
        # Unknown top-level key — fall back to linear scan via empty
        # SQL result, then the recall path handles correctness.
        return " WHERE 1=0", []

    return " WHERE " + " AND ".join(clauses), params


def recall_via_index(
    store_path: Path, filters: dict[str, Any]
) -> list[dict[str, Any]]:
    """Query the SQLite sidecar; raise :class:`IndexStaleError` if stale.

    Returns the same list-of-dicts shape that ``recall_parametric``
    builds for the linear-scan path so the two are interchangeable.
    """
    store_path = Path(store_path)
    sidecar = index_path_for(store_path)
    if not sidecar.is_file():
        raise IndexStaleError(
            f"sidecar missing for {store_path} (expected {sidecar})"
        )

    try:
        jsonl_mtime = store_path.stat().st_mtime
        sidecar_mtime = sidecar.stat().st_mtime
    except OSError as exc:
        raise IndexStaleError(str(exc)) from exc

    if jsonl_mtime > sidecar_mtime:
        raise IndexStaleError(
            "JSONL is newer than the sidecar — caller should rebuild "
            "or fall back to linear scan"
        )

    where_clause, params = _build_where_clause(filters or {})

    connection = sqlite3.connect(str(sidecar))
    try:
        connection.row_factory = sqlite3.Row
        sql = (
            "SELECT raw_row FROM parametric_records"
            + where_clause
            + " ORDER BY row_id ASC"
        )
        cursor = connection.execute(sql, params)
        rows = []
        for row in cursor.fetchall():
            try:
                parsed = json.loads(row["raw_row"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            rows.append(parsed)
    finally:
        connection.close()

    return rows


__all__ = [
    "DEFAULT_INDEX_THRESHOLD",
    "IndexStaleError",
    "build_or_refresh_index",
    "index_path_for",
    "needs_index",
    "recall_via_index",
]
