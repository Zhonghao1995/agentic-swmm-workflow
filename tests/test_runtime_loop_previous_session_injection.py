"""Startup injection: ``<previous-session>`` fence in the system prompt.

Seed the SQLite store with a finished session for ``case_name=tecnopolo``.
Start a new session for the same case. The next ``respond_with_tools``
call must include the ``<previous-session>`` fence quoting the prior
goal.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.runtime import run_openai_plan
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.providers.base import ProviderToolResponse


class _RecordingProvider:
    """Provider stub that records every ``system_prompt`` argument."""

    def __init__(self) -> None:
        self.model = "stub-model"
        self.captured: list[str] = []

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items,
        tools,
        previous_response_id=None,
    ) -> ProviderToolResponse:
        self.captured.append(system_prompt)
        # Return a no-tool response so the planner exits immediately.
        return ProviderToolResponse(
            text="ok",
            model=self.model,
            response_id="stub-1",
            tool_calls=[],
            raw={"stub": True},
        )


@pytest.fixture
def isolated_db(tmp_path, monkeypatch) -> Path:
    db_path = tmp_path / "sessions.sqlite"
    monkeypatch.setenv("AISWMM_SESSION_DB", str(db_path))
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(tmp_path / "curated"))
    yield db_path


def _seed_prior(db_path: Path) -> None:
    from agentic_swmm.memory import session_db

    session_db.initialize(db_path)
    with session_db.connect(db_path) as conn:
        session_db.upsert_session(
            conn,
            session_id="20260513_180000_tecnopolo_run",
            start_utc="2026-05-13T18:00:00+00:00",
            end_utc="2026-05-13T18:01:00+00:00",
            goal="plot the figure for the result",
            case_name="tecnopolo",
            planner="openai",
            model="gpt-5.5",
            ok=False,
        )
        conn.commit()


def test_previous_session_fence_lands_in_system_prompt(
    tmp_path, isolated_db, monkeypatch
) -> None:
    _seed_prior(isolated_db)
    # The runtime_loop's auto-router would short-circuit a SWMM-style
    # goal away from the planner. Disable it so the stub provider gets
    # the system prompt directly.
    monkeypatch.setenv("AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER", "1")

    # A new tecnopolo session under a date subdir, matching the
    # naming convention runtime_loop uses for case inference.
    session_dir = tmp_path / "2026-05-14" / "100000_tecnopolo_run"
    session_dir.mkdir(parents=True)
    trace_path = session_dir / "agent_trace.jsonl"

    from agentic_swmm.agent.runtime_loop import _build_system_prompt_extras

    extras = _build_system_prompt_extras(
        session_dir=session_dir,
        prior_session_state=None,
    )
    assert any("<previous-session" in extra for extra in extras), extras
    assert any('case="tecnopolo"' in extra for extra in extras)
    assert any("plot the figure for the result" in extra for extra in extras)

    provider = _RecordingProvider()
    registry = AgentToolRegistry()
    executor = AgentExecutor(registry, session_dir=session_dir, trace_path=trace_path)
    run_openai_plan(
        goal="re-plot todcreek using the corrected window",
        model="stub-model",
        provider=provider,
        registry=registry,
        executor=executor,
        max_steps=1,
        trace_path=trace_path,
        verbose=False,
        emit=lambda *args, **kwargs: None,
        prior_session_state=None,
        system_prompt_extras=extras,
    )
    assert provider.captured, "provider should have been called"
    system_prompt = provider.captured[0]
    assert "<previous-session" in system_prompt
    assert 'case="tecnopolo"' in system_prompt
    assert "plot the figure for the result" in system_prompt


def test_no_previous_session_block_for_fresh_case(
    tmp_path, isolated_db
) -> None:
    # No prior session seeded — the helper must return an empty extra
    # for the previous-session slot.
    from agentic_swmm.agent.runtime_loop import _build_system_prompt_extras

    session_dir = tmp_path / "2026-05-14" / "200000_newcase_run"
    session_dir.mkdir(parents=True)
    extras = _build_system_prompt_extras(session_dir=session_dir, prior_session_state=None)
    assert not any("<previous-session" in extra for extra in extras)
