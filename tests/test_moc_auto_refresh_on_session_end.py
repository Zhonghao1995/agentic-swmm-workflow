"""UX-5 (issue #60): session-end hook regenerates ``runs/INDEX.md``.

The 'living memory' promise breaks when chat sessions write new
``chat_note.md`` files but the MOC stays stale until ``aiswmm audit``
runs. This test drives a chat turn through ``run_openai_planner`` and
asserts that ``runs/INDEX.md`` is created (or its mtime advances) after
the session ends.
"""

from __future__ import annotations

import argparse
import time
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
            text="hello",
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
    monkeypatch.setenv("AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER", "1")
    monkeypatch.setattr(
        "agentic_swmm.agent.runtime_loop.load_config",
        lambda: type(
            "C",
            (),
            {
                "get": lambda self, key, default=None: "stub-model"
                if key.endswith("model")
                else "openai"
            },
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
def isolated_runs_root(tmp_path, monkeypatch):
    """Point session DB, facts dir, and runs root at the tmp tree."""
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True)
    monkeypatch.setenv("AISWMM_SESSION_DB", str(tmp_path / "sessions.sqlite"))
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(tmp_path / "curated"))
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(runs_root))
    return runs_root


def test_session_end_writes_runs_index_md(tmp_path, isolated_runs_root, monkeypatch) -> None:
    """A chat turn that ends successfully must leave runs/INDEX.md on disk."""
    session_dir = isolated_runs_root / "2026-05-14" / "120000_hello_chat"
    session_dir.mkdir(parents=True)
    trace_path = session_dir / "agent_trace.jsonl"

    index_path = isolated_runs_root / "INDEX.md"
    assert not index_path.exists(), "precondition: INDEX.md does not exist yet"

    rc = _run_one_chat_turn(
        session_dir=session_dir, trace_path=trace_path, monkeypatch=monkeypatch
    )
    assert rc == 0

    assert index_path.exists(), "session-end hook must create runs/INDEX.md"
    text = index_path.read_text(encoding="utf-8")
    assert "type: runs-index" in text


def test_session_end_refreshes_existing_runs_index(tmp_path, isolated_runs_root, monkeypatch) -> None:
    """When INDEX.md already exists, the hook must update its mtime."""
    session_dir = isolated_runs_root / "2026-05-14" / "120000_refresh_chat"
    session_dir.mkdir(parents=True)
    trace_path = session_dir / "agent_trace.jsonl"

    index_path = isolated_runs_root / "INDEX.md"
    # Seed a stale INDEX.md whose mtime is well in the past so the
    # post-session mtime cannot accidentally tie.
    index_path.write_text("stale\n", encoding="utf-8")
    stale_mtime = time.time() - 60
    import os

    os.utime(index_path, (stale_mtime, stale_mtime))
    stale_size = index_path.stat().st_size

    rc = _run_one_chat_turn(
        session_dir=session_dir, trace_path=trace_path, monkeypatch=monkeypatch
    )
    assert rc == 0

    assert index_path.stat().st_mtime > stale_mtime, (
        "session-end hook must refresh runs/INDEX.md mtime"
    )
    # The stale "stale\n" content (6 bytes) is much smaller than a
    # generated MOC, so size also growing is a second cross-check.
    assert index_path.stat().st_size > stale_size
    assert "type: runs-index" in index_path.read_text(encoding="utf-8")
