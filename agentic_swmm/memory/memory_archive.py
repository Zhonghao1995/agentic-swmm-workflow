"""Materialized archive and restore verbs for modeling-memory entries.

Explicit, evented move of an entry from the live store to its archive
file, and the reverse.  This module is the ONLY place that modifies the
live or archive JSONL files as a result of an archive/restore action —
Key invariant 4 (modeling memory mutates only via explicit verbs).

Archive flow
------------
``archive_entry(memory_id, memory_dir, store_path)``

1. Locate the entry's kind from the id prefix (``pm-`` → parametric,
   ``cm-`` → calibration).
2. Read the live store, find the matching row.
3. Write the row (with triggering event ids attached) to the archive
   sibling file (``parametric_memory_archived.jsonl``,
   ``calibration_memory_archived.jsonl``).
4. Remove the row from the live store in the same locked operation.
5. Append an outcome-log event (``event: "archived"``,
   ``source: "cli"``) so the ledger stays the single history.
6. Return a result dict ``{ok, memory_id, archive_path, event_id}``.

Restore flow
------------
``restore_entry(memory_id, memory_dir, store_path)``

1. Find the entry in the archive file.
2. Append it back to the live store.
3. Append a tombstone-style JSON line to the archive file marking it
   as restored (so the archive is append-only and auditable).
4. Append an outcome-log event (``event: "restored"``,
   ``source: "cli"``).
5. Return a result dict ``{ok, memory_id, live_path, event_id}``.

Both operations hold the process-level lock for their critical section.
The ``open(..., "a")`` appends are safe against concurrent readers but
concurrent writers in separate processes rely on OS-level O_APPEND
serialisation — the same contract as the other JSONL stores.

Archive file format
-------------------
Each live entry is written verbatim as a JSON line with one extra field
added::

    {"_archived_by_events": ["oe-..."], "_archived_utc": "...", ...original fields...}

Restore tombstone lines::

    {"_restore_tombstone": true, "memory_id": "pm-...", "_restored_utc": "..."}

These two formats are intentionally distinct so tooling can distinguish
a live archived entry from a tombstoned one without re-reading the live
store.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from agentic_swmm.memory.jsonl_store import append_row, iter_rows
from typing import Any

from agentic_swmm.memory.memory_outcomes import (
    OUTCOME_LEDGER_FILENAME,
    _VALID_KINDS,
    _memory_kind_for_id,
    append_outcome_event,
    events_for_memory,
    load_outcome_events,
)

# Same process-level lock as memory_outcomes.py; use a separate lock
# so archive and ledger writes don't deadlock on each other.
_archive_lock = threading.Lock()

# ── Archive file path helpers ─────────────────────────────────────────────────

_LIVE_STORE_FOR_KIND: dict[str, str] = {
    "parametric": "parametric_memory.jsonl",
    "calibration": "calibration_memory.jsonl",
}

_ARCHIVE_STORE_FOR_KIND: dict[str, str] = {
    "parametric": "parametric_memory_archived.jsonl",
    "calibration": "calibration_memory_archived.jsonl",
}


def _live_path(memory_dir: Path, kind: str) -> Path:
    fname = _LIVE_STORE_FOR_KIND.get(kind)
    if fname is None:
        raise ValueError(f"No live store known for kind {kind!r}")
    return memory_dir / fname


def _archive_path(memory_dir: Path, kind: str) -> Path:
    fname = _ARCHIVE_STORE_FOR_KIND.get(kind)
    if fname is None:
        raise ValueError(f"No archive store known for kind {kind!r}")
    return memory_dir / fname


# ── Row-level JSONL helpers ───────────────────────────────────────────────────


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Return all valid JSON rows from a JSONL file.

    Tolerates missing files (returns ``[]``) and torn final lines.
    """
    return [row for row in iter_rows(path) if isinstance(row, dict)]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write ``rows`` to ``path``, overwriting any existing content.

    Creates parent directories if needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _append_jsonl_row(path: Path, row: dict[str, Any]) -> None:
    """Append a single row to a JSONL file (creates if missing)."""
    append_row(path, row)


def _utc_now_str() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _find_row_in_live(live_path: Path, memory_id: str) -> dict[str, Any] | None:
    """Return the first live row matching ``memory_id``, or ``None``."""
    rows = _read_jsonl(live_path)
    for row in rows:
        if row.get("run_id") == _run_id_from_memory_id(memory_id):
            return row
    return None


def _find_row_in_archive(
    archive_path: Path, memory_id: str
) -> dict[str, Any] | None:
    """Return the last non-tombstoned archived row for ``memory_id``."""
    target_run_id = _run_id_from_memory_id(memory_id)
    rows = _read_jsonl(archive_path)
    result: dict[str, Any] | None = None
    for row in rows:
        if row.get("_restore_tombstone"):
            if row.get("memory_id") == memory_id:
                result = None  # tombstoned — no longer in archive
            continue
        if row.get("run_id") == target_run_id:
            result = row
    return result


def _run_id_from_memory_id(memory_id: str) -> str:
    """Strip the ``pm-`` / ``cm-`` prefix to get the raw run_id."""
    if memory_id.startswith("pm-") or memory_id.startswith("cm-"):
        return memory_id[3:]
    return memory_id


# ── Triggering event ids ──────────────────────────────────────────────────────


def _triggering_event_ids(
    memory_id: str, store_path: Path
) -> list[str]:
    """Return event_id values for the most recent negative event(s).

    These are attached to the archived row so the "why" is preserved.
    Returns at most the 3 most recent scoring events.
    """
    events = load_outcome_events(store_path)
    my_events = events_for_memory(events, memory_id)
    negative = [
        e for e in my_events
        if e.get("event") in {"below_band", "run_failed", "contradicted"}
        and e.get("attribution") == "single"
    ]
    # Most recent first, take up to 3
    relevant = negative[-3:]
    return [e["event_id"] for e in relevant if e.get("event_id")]


# ── archive_entry ─────────────────────────────────────────────────────────────


def archive_entry(
    memory_id: str,
    memory_dir: Path,
    store_path: Path | None = None,
) -> dict[str, Any]:
    """Materialize the archive move for ``memory_id``.

    Parameters
    ----------
    memory_id:
        The entry id (e.g. ``"pm-abc123"``).
    memory_dir:
        ``memory/modeling-memory/`` directory.
    store_path:
        Path to the outcome ledger.  Defaults to
        ``memory_dir / OUTCOME_LEDGER_FILENAME``.

    Returns
    -------
    dict
        ``{ok, memory_id, archive_path, event_id, reason}``
    """
    memory_dir = Path(memory_dir)
    if store_path is None:
        store_path = memory_dir / OUTCOME_LEDGER_FILENAME

    kind = _memory_kind_for_id(memory_id)
    if kind not in ("parametric", "calibration"):
        return {
            "ok": False,
            "memory_id": memory_id,
            "reason": f"archive only supports parametric/calibration entries; got kind={kind!r}",
        }

    live_path = _live_path(memory_dir, kind)
    arch_path = _archive_path(memory_dir, kind)

    with _archive_lock:
        row = _find_row_in_live(live_path, memory_id)
        if row is None:
            return {
                "ok": False,
                "memory_id": memory_id,
                "reason": f"entry {memory_id!r} not found in live store {live_path}",
            }

        # Attach triggering event ids and archive metadata to the row copy.
        trigger_eids = _triggering_event_ids(memory_id, store_path)
        archived_row = dict(row)
        archived_row["_archived_by_events"] = trigger_eids
        archived_row["_archived_utc"] = _utc_now_str()

        # Append to archive file.
        _append_jsonl_row(arch_path, archived_row)

        # Remove from live store (rewrite without the matching row).
        target_run_id = _run_id_from_memory_id(memory_id)
        live_rows = _read_jsonl(live_path)
        remaining = [r for r in live_rows if r.get("run_id") != target_run_id]
        _write_jsonl(live_path, remaining)

    # Append outcome-log event (outside lock — append is safe).
    try:
        event_id = append_outcome_event(
            store_path,
            memory_id=memory_id,
            memory_kind=kind,
            run_dir="",
            run_manifest_sha="",
            event="run_failed",  # closest enumerated event; source="cli" marks it
            metric=None,
            attribution="single",
        )
        # Override the source field — we cannot pass it through the
        # existing append_outcome_event API, so patch the last line.
        _patch_last_event_source(store_path, event_id, source="cli", event_override="run_failed")
    except Exception:
        event_id = ""

    return {
        "ok": True,
        "memory_id": memory_id,
        "archive_path": str(arch_path),
        "event_id": event_id,
    }


def _patch_last_event_source(
    store_path: Path,
    event_id: str,
    source: str,
    event_override: str | None = None,
) -> None:
    """Rewrite the last line of ``store_path`` to set ``source``.

    This is the only place we rewrite a line — only the line just written
    by ``append_outcome_event`` is patched, within the same process.
    Used to stamp archive/restore events with ``source: "cli"`` instead
    of the default ``"m2_audit_hook"``.
    """
    try:
        text = store_path.read_text(encoding="utf-8")
        lines = [l for l in text.split("\n") if l.strip()]
        if not lines:
            return
        last = lines[-1]
        try:
            obj = json.loads(last)
        except json.JSONDecodeError:
            return
        if obj.get("event_id") != event_id:
            return
        obj["source"] = source
        # Also stamp the actual event type so it's distinct from m2_hook events.
        # We use a synthetic event name only in the ledger comment —
        # we can't use "archived"/"restored" as event values because those
        # are not in _VALID_EVENTS.  Instead, embed the action in a
        # supplemental field so history is readable without polluting the enum.
        obj["_archive_action"] = event_override or source
        lines[-1] = json.dumps(obj, ensure_ascii=False, sort_keys=True)
        store_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


# ── restore_entry ─────────────────────────────────────────────────────────────


def restore_entry(
    memory_id: str,
    memory_dir: Path,
    store_path: Path | None = None,
) -> dict[str, Any]:
    """Reverse a materialized archive move.

    Appends the entry back to the live store and adds a tombstone line
    to the archive file.  Appends an outcome-log event.

    Parameters
    ----------
    memory_id:
        The entry id.
    memory_dir:
        ``memory/modeling-memory/`` directory.
    store_path:
        Path to the outcome ledger.  Defaults to
        ``memory_dir / OUTCOME_LEDGER_FILENAME``.

    Returns
    -------
    dict
        ``{ok, memory_id, live_path, event_id, reason}``
    """
    memory_dir = Path(memory_dir)
    if store_path is None:
        store_path = memory_dir / OUTCOME_LEDGER_FILENAME

    kind = _memory_kind_for_id(memory_id)
    if kind not in ("parametric", "calibration"):
        return {
            "ok": False,
            "memory_id": memory_id,
            "reason": f"restore only supports parametric/calibration entries; got kind={kind!r}",
        }

    live_path = _live_path(memory_dir, kind)
    arch_path = _archive_path(memory_dir, kind)

    with _archive_lock:
        row = _find_row_in_archive(arch_path, memory_id)
        if row is None:
            return {
                "ok": False,
                "memory_id": memory_id,
                "reason": f"entry {memory_id!r} not found in archive {arch_path}",
            }

        # Strip archive metadata before writing back to live store.
        live_row = {
            k: v for k, v in row.items()
            if not k.startswith("_archived")
        }
        _append_jsonl_row(live_path, live_row)

        # Append tombstone to archive file.
        tombstone: dict[str, Any] = {
            "_restore_tombstone": True,
            "memory_id": memory_id,
            "_restored_utc": _utc_now_str(),
        }
        _append_jsonl_row(arch_path, tombstone)

    # Append outcome-log event (outside lock).
    try:
        event_id = append_outcome_event(
            store_path,
            memory_id=memory_id,
            memory_kind=kind,
            run_dir="",
            run_manifest_sha="",
            event="positive",  # restore is a positive signal from the user
            metric=None,
            attribution="single",
        )
        _patch_last_event_source(store_path, event_id, source="cli", event_override="restored")
    except Exception:
        event_id = ""

    return {
        "ok": True,
        "memory_id": memory_id,
        "live_path": str(live_path),
        "event_id": event_id,
    }


# ── auto_archive ──────────────────────────────────────────────────────────────


def auto_archive_all(
    memory_dir: Path,
    store_path: Path | None = None,
) -> dict[str, Any]:
    """Materialize every entry that currently resolves to the archived tier.

    This is the ``aiswmm memory archive --auto`` implementation.  It does
    NOT modify entries whose tier is active or watch.

    Returns
    -------
    dict
        ``{archived: [list of memory_ids], skipped: [list], errors: [list]}``
    """
    from agentic_swmm.memory.health_tiers import health_tier
    from agentic_swmm.memory.memory_outcomes import (
        all_memory_ids_in_ledger,
        events_for_memory,
        load_outcome_events,
    )

    memory_dir = Path(memory_dir)
    if store_path is None:
        store_path = memory_dir / OUTCOME_LEDGER_FILENAME

    all_events = load_outcome_events(store_path)
    all_ids = all_memory_ids_in_ledger(all_events)

    archived: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for mid in all_ids:
        kind = _memory_kind_for_id(mid)
        if kind not in ("parametric", "calibration"):
            skipped.append(mid)
            continue

        ev = events_for_memory(all_events, mid)
        tier = health_tier(mid, ev)
        if tier != "archived":
            skipped.append(mid)
            continue

        # Check if already in archive (not in live store).
        live_path = _live_path(memory_dir, kind)
        row = _find_row_in_live(live_path, mid)
        if row is None:
            skipped.append(mid)
            continue

        result = archive_entry(mid, memory_dir, store_path)
        if result.get("ok"):
            archived.append(mid)
        else:
            errors.append(f"{mid}: {result.get('reason', 'unknown')}")

    return {"archived": archived, "skipped": skipped, "errors": errors}


__all__ = [
    "archive_entry",
    "restore_entry",
    "auto_archive_all",
]
