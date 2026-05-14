"""Handler tests for the ``recall_session_history`` tool.

Verifies the tool is registered, carries the routing description,
returns FTS5-matched sessions wrapped in a ``<memory-context source="sessions">``
fence, and respects the optional ``case_name`` filter.
"""

from __future__ import annotations

import os
from pathlib import Path


def _seed(db_path: Path) -> None:
    from agentic_swmm.memory import session_db

    session_db.initialize(db_path)
    with session_db.connect(db_path) as conn:
        for sid, case, goal, message in (
            (
                "20260513_220000_todcreek_run",
                "todcreek",
                "todcreek peak flow at outfall",
                "todcreek peak flow run finished with manifest.json",
            ),
            (
                "20260513_220500_todcreek_chat",
                "todcreek",
                "plot the peak flow figure for todcreek",
                "plot_run produced the peak flow hydrograph at O1",
            ),
            (
                "20260513_221000_tecnopolo_run",
                "tecnopolo",
                "tecnopolo calibration",
                "tecnopolo calibration with observed flow series",
            ),
        ):
            session_db.upsert_session(
                conn,
                session_id=sid,
                start_utc="2026-05-13T22:00:00+00:00",
                end_utc="2026-05-13T22:01:00+00:00",
                goal=goal,
                case_name=case,
                planner="openai",
                model="gpt-5.5",
                ok=True,
            )
            session_db.insert_message(
                conn,
                session_id=sid,
                step=1,
                role="user",
                text=goal,
                utc="2026-05-13T22:00:00+00:00",
            )
            session_db.insert_message(
                conn,
                session_id=sid,
                step=1,
                role="assistant",
                text=message,
                utc="2026-05-13T22:00:30+00:00",
            )
        conn.commit()


def test_recall_session_history_returns_matched_sessions_under_memory_context(
    tmp_path: Path,
) -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry
    from agentic_swmm.agent.types import ToolCall

    db_path = tmp_path / "sessions.sqlite"
    _seed(db_path)
    os.environ["AISWMM_SESSION_DB"] = str(db_path)
    try:
        registry = AgentToolRegistry()
        result = registry.execute(
            ToolCall(
                "recall_session_history",
                {"query": "todcreek peak flow", "case_name": "todcreek", "limit": 5},
            ),
            session_dir=tmp_path / "session",
        )
    finally:
        os.environ.pop("AISWMM_SESSION_DB", None)

    assert result["ok"] is True
    excerpt = result.get("excerpt", "")
    assert '<memory-context source="sessions"' in excerpt
    matched_ids = {hit["session_id"] for hit in result["results"]}
    assert "20260513_220000_todcreek_run" in matched_ids or "20260513_220500_todcreek_chat" in matched_ids
    # case_name filter excludes the tecnopolo session.
    assert "20260513_221000_tecnopolo_run" not in matched_ids
    # Payload should stay within the budget the spec asks for (~1000 tokens).
    assert len(excerpt.split()) < 1500


def test_recall_session_history_handles_missing_store(tmp_path: Path) -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry
    from agentic_swmm.agent.types import ToolCall

    db_path = tmp_path / "absent.sqlite"
    os.environ["AISWMM_SESSION_DB"] = str(db_path)
    try:
        registry = AgentToolRegistry()
        result = registry.execute(
            ToolCall("recall_session_history", {"query": "anything"}),
            session_dir=tmp_path / "session",
        )
    finally:
        os.environ.pop("AISWMM_SESSION_DB", None)
    assert result["ok"] is True
    assert result["results"] == []
    assert "store not initialised" in result["summary"]


def test_recall_session_history_tool_is_registered_with_routing_text() -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry

    registry = AgentToolRegistry()
    schemas = {s["name"]: s for s in registry.schemas()}
    assert "recall_session_history" in schemas
    desc = schemas["recall_session_history"]["description"]
    assert "USE WHEN" in desc
    assert "DO NOT USE WHEN" in desc
    assert registry.is_read_only("recall_session_history") is True
