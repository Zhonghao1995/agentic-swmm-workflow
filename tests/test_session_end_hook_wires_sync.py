"""Wiring test: ``runtime_loop`` actually calls the session sync hook.

A stub provider returns no tool calls so the planner exits after one
turn. After ``run_openai_planner`` returns we assert that the just-
finished session shows up in the SQLite store — proving the hook is
wired in, not just defined as parallel dead code.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agentic_swmm.providers.base import ProviderToolResponse


class _NoopProvider:
    """Stub: returns an immediate text response with no tool calls."""

    def __init__(self) -> None:
        self.model = "stub-model"

    def respond_with_tools(
        self, *, system_prompt, input_items, tools, previous_response_id=None
    ) -> ProviderToolResponse:
        return ProviderToolResponse(
            text="done",
            model=self.model,
            response_id="stub-1",
            tool_calls=[],
            raw={"stub": True},
        )


def _run_one_chat_turn(
    *,
    session_dir: Path,
    trace_path: Path,
    monkeypatch,
) -> int:
    import agentic_swmm.agent.runtime_loop as runtime_loop
    from agentic_swmm.agent.tool_registry import AgentToolRegistry

    monkeypatch.setattr(
        "agentic_swmm.agent.runtime_loop.OpenAIProvider",
        lambda *args, **kwargs: _NoopProvider(),
    )
    # Avoid the auto workflow router so the planner reaches the
    # provider stub and a clean session_end fires.
    monkeypatch.setenv("AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER", "1")

    # Pretend the OpenAI model is configured so run_openai_planner
    # passes its argument validation.
    monkeypatch.setattr(
        "agentic_swmm.agent.runtime_loop.load_config",
        lambda: type(
            "C",
            (),
            {"get": lambda self, key, default=None: "stub-model" if key.endswith("model") else "openai"},
        )(),
    )
    args = argparse.Namespace(
        planner="openai",
        provider="openai",
        model="stub-model",
        max_steps=1,
        verbose=False,
        dry_run=False,
        quick=False,
    )
    return runtime_loop.run_openai_planner(
        args,
        goal="say hi",
        session_dir=session_dir,
        trace_path=trace_path,
        registry=AgentToolRegistry(),
        chat_session=True,
    )


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.sqlite"
    monkeypatch.setenv("AISWMM_SESSION_DB", str(db_path))
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(tmp_path / "curated"))
    return db_path


def test_session_end_hook_populates_sqlite_store(tmp_path, isolated, monkeypatch) -> None:
    session_dir = tmp_path / "2026-05-14" / "120000_hello_chat"
    session_dir.mkdir(parents=True)
    trace_path = session_dir / "agent_trace.jsonl"

    rc = _run_one_chat_turn(
        session_dir=session_dir, trace_path=trace_path, monkeypatch=monkeypatch
    )
    assert rc == 0

    assert isolated.exists(), "runtime_loop must have created the SQLite store"

    from agentic_swmm.memory import session_db

    with session_db.connect(isolated) as conn:
        sids = session_db.list_session_ids(conn)
    assert sids, "session_end hook should have inserted the session"
    assert any("hello" in sid for sid in sids)
