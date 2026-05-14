"""End-of-session JSONL -> SQLite projector.

``runtime_loop`` calls :func:`sync_session_to_db` at the end of every
turn (chat or SWMM run). The function reads ``agent_trace.jsonl`` plus
``session_state.json`` from the just-finished session directory and
projects them into the cross-session SQLite store.

The same projector is reused by ``scripts/backfill_sessions.py`` so the
live path and the migration path share one code surface.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agentic_swmm.memory import session_db
from agentic_swmm.memory.case_inference import infer_case_name


def default_db_path(repo_root: Path | None = None) -> Path:
    """Return the canonical store path under ``runs/sessions.sqlite``.

    Honours ``AISWMM_SESSION_DB`` for tests, otherwise resolves
    against the supplied ``repo_root`` (or the package's repo root).
    """
    override = os.environ.get("AISWMM_SESSION_DB")
    if override:
        return Path(override)
    if repo_root is None:
        from agentic_swmm.utils.paths import repo_root as _repo_root

        repo_root = _repo_root()
    return repo_root / "runs" / "sessions.sqlite"


def sync_session_to_db(
    session_dir: Path,
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Project the just-finished session at ``session_dir`` into SQLite.

    Returns a dict reporting what was written so callers can log it.
    Returns ``{"ok": False, ...}`` on missing inputs; the function
    never raises for a missing trace file because end-of-session hooks
    should not fail the user-facing turn.
    """
    if db_path is None:
        db_path = default_db_path()
    summary: dict[str, Any] = {
        "ok": False,
        "session_id": None,
        "messages": 0,
        "tool_events": 0,
        "db_path": str(db_path),
    }
    if not session_dir.exists() or not session_dir.is_dir():
        summary["reason"] = f"session_dir missing: {session_dir}"
        return summary

    trace_path = session_dir / "agent_trace.jsonl"
    state_path = session_dir / "session_state.json"
    if not trace_path.exists():
        summary["reason"] = f"agent_trace.jsonl missing: {trace_path}"
        return summary

    events = list(_iter_trace_events(trace_path))
    session_state: dict[str, Any] = {}
    if state_path.exists():
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            session_state = payload

    session_id = session_db.session_id_from_dir(session_dir)
    summary["session_id"] = session_id

    case_name = (
        session_state.get("case_name")
        if isinstance(session_state.get("case_name"), str) and session_state.get("case_name").strip()
        else infer_case_name(session_state)
    )
    goal = session_state.get("goal")
    planner = session_state.get("planner")
    model = session_state.get("model")
    ok_value = session_state.get("ok")
    if ok_value is None and session_state.get("status"):
        ok_value = str(session_state.get("status")).lower() == "ok"
    start_utc, end_utc = _bracket_times(events, fallback=session_state.get("created_at_utc"))

    messages = session_db.chunked_messages_from_events(events)
    tool_events = session_db.tool_events_from_trace(events)

    session_db.initialize(db_path)
    with session_db.connect(db_path) as conn:
        session_db.upsert_session(
            conn,
            session_id=session_id,
            start_utc=start_utc,
            end_utc=end_utc,
            goal=str(goal) if goal else None,
            case_name=str(case_name) if case_name else None,
            planner=str(planner) if planner else None,
            model=str(model) if model else None,
            ok=bool(ok_value) if ok_value is not None else None,
        )
        for row in messages:
            session_db.insert_message(
                conn,
                session_id=session_id,
                step=int(row["step"]),
                role=str(row["role"]),
                text=str(row["text"]),
                utc=row.get("utc"),
            )
        for row in tool_events:
            session_db.insert_tool_event(
                conn,
                session_id=session_id,
                step=int(row["step"]),
                kind=str(row["kind"]),
                tool_name=str(row["tool_name"] or ""),
                args=row.get("args") or {},
                ok=row.get("ok"),
                summary=row.get("summary"),
                stderr_tail=row.get("stderr_tail"),
                utc=row.get("utc"),
            )
        conn.commit()

    summary["ok"] = True
    summary["messages"] = len(messages)
    summary["tool_events"] = len(tool_events)
    summary["case_name"] = case_name
    return summary


def _iter_trace_events(trace_path: Path):
    raw = trace_path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


def _bracket_times(
    events: list[dict[str, Any]],
    *,
    fallback: str | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(start_utc, end_utc)`` derived from the trace events."""
    start_utc: str | None = None
    end_utc: str | None = None
    for event in events:
        utc = event.get("timestamp_utc")
        if not isinstance(utc, str):
            continue
        if start_utc is None:
            start_utc = utc
        end_utc = utc
    if start_utc is None and fallback:
        start_utc = fallback
    if end_utc is None:
        end_utc = start_utc
    return start_utc, end_utc
