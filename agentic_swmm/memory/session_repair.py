"""Non-destructive repair for ``runs/sessions.sqlite`` (issue #204).

Back up the corrupt store, then rebuild it by projecting every
``session_state.json`` + sibling ``agent_trace.jsonl`` under the runs
root through the live sync projector. Sits next to ``session_db`` /
``session_sync`` as the recovery member of that family — it lived
inside the ``aiswmm memory`` CLI verb module until the 2026-07
architecture pass, which made it importable for programmatic callers
(e.g. a future ``doctor --fix``) without dragging argparse in.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def repair_sessions_db(
    runs_dir: Path,
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Back up the corrupt sessions.sqlite and rebuild it from traces.

    Non-destructive: when ``db_path`` already exists, its bytes are
    moved to ``sessions.sqlite.corrupt-<UTC timestamp>`` BEFORE any
    rebuild begins. The rebuild then walks ``runs_dir`` for every
    ``session_state.json`` and projects the sibling
    ``agent_trace.jsonl`` into a fresh DB via the live sync projector.

    Returns a summary dict::

        {
            "ok": bool,
            "backup": "<path>" | None,         # None when no original to back up
            "sessions_rebuilt": int,
            "messages_rebuilt": int,
            "tool_events_rebuilt": int,
            "failures": [str, ...],
            "db_path": "<path>",
        }
    """
    from agentic_swmm.memory import session_db
    from agentic_swmm.memory.session_sync import sync_session_to_db

    runs_dir = Path(runs_dir)
    if db_path is None:
        db_path = runs_dir / "sessions.sqlite"
    db_path = Path(db_path)

    summary: dict[str, Any] = {
        "ok": False,
        "backup": None,
        "sessions_rebuilt": 0,
        "messages_rebuilt": 0,
        "tool_events_rebuilt": 0,
        "failures": [],
        "db_path": str(db_path),
    }

    # ---- 0. Refuse if the file is "unreadable" (locked / permission
    # denied) — the DB itself might be healthy and repair would
    # overwrite it. Issue #204 review HIGH finding.
    if db_path.exists():
        pre_report = session_db.integrity_check(db_path)
        if pre_report.state == "unreadable":
            summary["failures"].append(
                f"{db_path}: file is unreadable ({pre_report.errors[0] if pre_report.errors else 'access denied'}); "
                "fix permissions or wait for another writer to release the file. "
                "Refusing to repair because the database may be healthy."
            )
            # ok stays False; do not back up or rebuild
            return summary

    # ---- 1. Back up the existing file (if any) before touching it.
    if db_path.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = db_path.with_name(f"{db_path.name}.corrupt-{timestamp}")
        # Pick a unique name if a backup from this same second already
        # exists (e.g. a tight retry); never overwrite a previous
        # backup.
        if backup_path.exists():
            counter = 1
            while True:
                candidate = db_path.with_name(
                    f"{db_path.name}.corrupt-{timestamp}-{counter}"
                )
                if not candidate.exists():
                    backup_path = candidate
                    break
                counter += 1
        # ``os.replace`` is atomic on a same-filesystem target (POSIX
        # rename(2)). On cross-filesystem moves it raises ``OSError`` —
        # which is desirable here: the corrupt DB and its backup MUST
        # land on the same FS so the rebuild step has a guaranteed
        # rollback target. ``shutil.move`` would silently degrade to
        # copy+unlink and the user would learn about the partial state
        # only when one of the two halves later disappeared.
        try:
            os.replace(str(db_path), str(backup_path))
        except OSError as exc:
            # Backup itself failed — disk full, permission denied, or
            # cross-filesystem rename. Refuse to touch the original.
            summary["failures"].append(
                f"could not back up {db_path} -> {backup_path}: {exc}; "
                "aborting repair to protect your data"
            )
            return summary
        summary["backup"] = str(backup_path)

    # ---- 2. Discover every session dir under runs_dir.
    session_dirs: list[Path] = []
    if runs_dir.exists():
        for state in runs_dir.rglob("session_state.json"):
            if state.is_file():
                session_dirs.append(state.parent)
    session_dirs.sort()

    # ---- 3. Initialise a fresh DB and project each session into it.
    session_db.initialize(db_path)
    for session_dir in session_dirs:
        try:
            sync = sync_session_to_db(session_dir, db_path=db_path)
        except Exception as exc:  # pragma: no cover - defensive
            summary["failures"].append(f"{session_dir}: {exc}")
            continue
        if sync.get("ok"):
            summary["sessions_rebuilt"] += 1
            summary["messages_rebuilt"] += int(sync.get("messages") or 0)
            summary["tool_events_rebuilt"] += int(sync.get("tool_events") or 0)
        else:
            summary["failures"].append(
                f"{session_dir}: {sync.get('reason', 'unknown')}"
            )

    # ---- 4. Bust the integrity cache so the next doctor / sync run
    # re-probes the new healthy file rather than serving the stale
    # corrupt verdict.
    session_db.clear_integrity_cache()

    # Issue #204 review MEDIUM finding: ok was unconditionally True,
    # hiding partial failures from CI / scripted callers. Only succeed
    # when zero sessions failed AND we actually rebuilt something (or
    # the runs dir was legitimately empty).
    summary["ok"] = not summary["failures"]
    return summary


def _preview_repair(runs_root: Path, db_path: Path) -> dict[str, Any]:
    """Return a preview of what ``repair_sessions_db`` would do.

    Walks the same paths as the real call but writes nothing — the
    user can see the would-be backup name and the count of sessions
    that would be rebuilt before committing to an irreversible move.
    """
    from datetime import datetime, timezone

    candidate_backup: str | None = None
    if db_path.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        candidate_backup = str(db_path.with_name(f"{db_path.name}.corrupt-{timestamp}"))
    session_count = 0
    if runs_root.exists():
        for state in runs_root.rglob("session_state.json"):
            if state.is_file():
                session_count += 1
    return {
        "would_back_up_to": candidate_backup,
        "would_rebuild_sessions": session_count,
    }


