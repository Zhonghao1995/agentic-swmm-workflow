"""End-of-session JSONL -> SQLite projector contract.

Given a fixture session_dir with both ``agent_trace.jsonl`` and
``session_state.json``, :func:`sync_session_to_db` must populate the
three tables idempotently: re-running adds zero new rows.
"""

from __future__ import annotations

import json
from pathlib import Path


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def _make_fixture_session(tmp_path: Path) -> Path:
    session_dir = tmp_path / "2026-05-13" / "224244_todcreek_run"
    trace = session_dir / "agent_trace.jsonl"
    state = session_dir / "session_state.json"
    events = [
        {
            "event": "session_start",
            "goal": "plot todcreek peak flow",
            "timestamp_utc": "2026-05-13T22:42:44+00:00",
        },
        {
            "event": "planner_response",
            "step": 1,
            "text": "I will inspect and plot.",
            "tool_calls": [
                {
                    "call_id": "c1",
                    "tool": "inspect_plot_options",
                    "args": {"run_dir": "runs/2026-05-13/224244_todcreek_run"},
                }
            ],
            "timestamp_utc": "2026-05-13T22:42:50+00:00",
        },
        {
            "event": "tool_result",
            "tool": "plot_run",
            "args": {"node": "O1"},
            "ok": True,
            "summary": "plot=runs/.../fig.png",
            "timestamp_utc": "2026-05-13T22:43:00+00:00",
        },
        {
            "event": "session_end",
            "ok": True,
            "final_text": "Plot completed.",
            "timestamp_utc": "2026-05-13T22:43:10+00:00",
        },
    ]
    _write_jsonl(trace, events)
    state_payload = {
        "goal": "plot todcreek peak flow",
        "planner": "openai",
        "model": "gpt-5.5",
        "ok": True,
        "workflow_state": {"active_run_dir": str(session_dir)},
    }
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(state_payload, indent=2), encoding="utf-8")
    return session_dir


def test_sync_session_writes_session_messages_and_tool_events(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db
    from agentic_swmm.memory.session_sync import sync_session_to_db

    session_dir = _make_fixture_session(tmp_path)
    db_path = tmp_path / "sessions.sqlite"

    summary = sync_session_to_db(session_dir, db_path=db_path)
    assert summary["ok"] is True
    assert summary["messages"] >= 2
    assert summary["tool_events"] >= 1

    with session_db.connect(db_path) as conn:
        sids = session_db.list_session_ids(conn)
        assert summary["session_id"] in sids
        messages = session_db.session_messages(conn, summary["session_id"])
        roles = {row["role"] for row in messages}
        assert "user" in roles and "assistant" in roles
        events = session_db.session_tool_events(conn, summary["session_id"])
        tool_names = {row["tool_name"] for row in events}
        assert "inspect_plot_options" in tool_names or "plot_run" in tool_names


def test_sync_session_is_idempotent_on_rerun(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db
    from agentic_swmm.memory.session_sync import sync_session_to_db

    session_dir = _make_fixture_session(tmp_path)
    db_path = tmp_path / "sessions.sqlite"

    sync_session_to_db(session_dir, db_path=db_path)
    with session_db.connect(db_path) as conn:
        first_msg = len(session_db.session_messages(conn, session_db.session_id_from_dir(session_dir)))
        first_tool = len(session_db.session_tool_events(conn, session_db.session_id_from_dir(session_dir)))

    # Re-run twice to simulate end-of-session hook + atexit fallback.
    sync_session_to_db(session_dir, db_path=db_path)
    sync_session_to_db(session_dir, db_path=db_path)
    with session_db.connect(db_path) as conn:
        sid = session_db.session_id_from_dir(session_dir)
        assert len(session_db.session_messages(conn, sid)) == first_msg
        assert len(session_db.session_tool_events(conn, sid)) == first_tool


def test_sync_handles_missing_trace_without_raising(tmp_path: Path) -> None:
    from agentic_swmm.memory.session_sync import sync_session_to_db

    empty = tmp_path / "no_trace"
    empty.mkdir()
    summary = sync_session_to_db(empty, db_path=tmp_path / "sessions.sqlite")
    assert summary["ok"] is False
    assert "missing" in summary.get("reason", "")
