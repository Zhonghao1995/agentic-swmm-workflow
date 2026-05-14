"""UX-5 (issue #60): MOC failure must not block session exit.

The session-end hook calls ``moc_generator.generate_moc`` best-effort.
If MOC generation raises (corrupt frontmatter, disk-full, anything),
the user's turn still exits 0 and a one-line warning is logged. This
test forces ``generate_moc`` to raise and asserts both invariants.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pytest

from agentic_swmm.providers.base import ProviderToolResponse


class _NoopProvider:
    def __init__(self) -> None:
        self.model = "stub-model"

    def respond_with_tools(
        self, *, system_prompt, input_items, tools, previous_response_id=None
    ) -> ProviderToolResponse:
        return ProviderToolResponse(
            text="ok",
            model=self.model,
            response_id="stub-1",
            tool_calls=[],
            raw={"stub": True},
        )


@pytest.fixture
def isolated_runs_root(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True)
    monkeypatch.setenv("AISWMM_SESSION_DB", str(tmp_path / "sessions.sqlite"))
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(tmp_path / "curated"))
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(runs_root))
    return runs_root


def test_moc_failure_does_not_crash_session_exit(
    tmp_path, isolated_runs_root, monkeypatch, caplog
) -> None:
    """When generate_moc raises, the chat turn must still return 0."""
    import agentic_swmm.agent.runtime_loop as runtime_loop
    from agentic_swmm.agent.tool_registry import AgentToolRegistry

    session_dir = isolated_runs_root / "2026-05-14" / "120000_boom_chat"
    session_dir.mkdir(parents=True)
    trace_path = session_dir / "agent_trace.jsonl"

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

    def _boom(*_args, **_kwargs):
        raise RuntimeError("synthetic MOC failure")

    # The runtime hook resolves ``generate_moc`` via the module attribute
    # so a monkeypatch on the attribute is enough to inject the failure.
    monkeypatch.setattr("agentic_swmm.agent.runtime_loop.generate_moc", _boom)

    args = argparse.Namespace(
        planner="openai",
        provider="openai",
        model="stub-model",
        max_steps=1,
        verbose=False,
        dry_run=False,
        quick=False,
    )

    with caplog.at_level(logging.WARNING):
        rc = runtime_loop.run_openai_planner(
            args,
            goal="say hi",
            session_dir=session_dir,
            trace_path=trace_path,
            registry=AgentToolRegistry(),
            chat_session=True,
        )

    assert rc == 0, "MOC failure must NOT crash the session — exit code must be 0"

    # The hook must leave a trace of the failure for the operator. We
    # accept either a logging.WARNING record or a stdout warning line.
    warned_in_log = any(
        "MOC" in record.message or "INDEX.md" in record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )
    assert warned_in_log, (
        "session-end hook must log a warning when MOC regen fails; "
        f"records={[(r.levelname, r.message) for r in caplog.records]}"
    )

    # And there must be NO runs/INDEX.md because the generator never
    # produced one (the hook must not write a stale or empty file).
    index_path = isolated_runs_root / "INDEX.md"
    assert not index_path.exists(), (
        "MOC failure must not produce a partial INDEX.md"
    )
