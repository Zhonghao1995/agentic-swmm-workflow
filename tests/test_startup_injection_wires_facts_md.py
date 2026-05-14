"""Wiring test: ``runtime_loop`` injects ``facts.md`` into the system prompt.

Write a fixture ``facts.md`` and start a session through
``run_openai_planner``. The stub provider's first ``respond_with_tools``
call must contain the facts text under a ``<project-facts>`` fence.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agentic_swmm.providers.base import ProviderToolResponse


class _CapturingProvider:
    def __init__(self) -> None:
        self.model = "stub-model"
        self.system_prompts: list[str] = []

    def respond_with_tools(
        self, *, system_prompt, input_items, tools, previous_response_id=None
    ) -> ProviderToolResponse:
        self.system_prompts.append(system_prompt)
        return ProviderToolResponse(
            text="done",
            model=self.model,
            response_id="stub",
            tool_calls=[],
            raw={"stub": True},
        )


def _write_facts(curated_dir: Path, text: str) -> Path:
    curated_dir.mkdir(parents=True, exist_ok=True)
    facts = curated_dir / "facts.md"
    facts.write_text(
        "<!-- WHEN TO PROPOSE: stuff -->\n"
        "# Project facts (curated)\n\n"
        f"{text}\n",
        encoding="utf-8",
    )
    return facts


def test_project_facts_fence_lands_in_system_prompt(tmp_path, monkeypatch) -> None:
    curated = tmp_path / "curated"
    _write_facts(curated, "- The Tod Creek outfall is canonical at node O1.")
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(curated))
    monkeypatch.setenv("AISWMM_SESSION_DB", str(tmp_path / "sessions.sqlite"))
    monkeypatch.setenv("AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER", "1")

    provider = _CapturingProvider()

    import agentic_swmm.agent.runtime_loop as runtime_loop
    from agentic_swmm.agent.tool_registry import AgentToolRegistry

    monkeypatch.setattr(
        "agentic_swmm.agent.runtime_loop.OpenAIProvider", lambda *a, **kw: provider
    )
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
    session_dir = tmp_path / "2026-05-14" / "100000_hello_chat"
    session_dir.mkdir(parents=True)
    trace = session_dir / "agent_trace.jsonl"
    rc = runtime_loop.run_openai_planner(
        args,
        goal="hi",
        session_dir=session_dir,
        trace_path=trace,
        registry=AgentToolRegistry(),
        chat_session=True,
    )
    assert rc == 0
    assert provider.system_prompts, "stub provider must have been called"
    first = provider.system_prompts[0]
    assert "<project-facts" in first
    assert "Tod Creek outfall is canonical at node O1" in first


def test_no_project_facts_fence_when_facts_md_is_empty(tmp_path, monkeypatch) -> None:
    curated = tmp_path / "curated"
    curated.mkdir()
    # Only the header → injection helper returns empty string.
    (curated / "facts.md").write_text("# Project facts (curated)\n", encoding="utf-8")
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(curated))
    monkeypatch.setenv("AISWMM_SESSION_DB", str(tmp_path / "sessions.sqlite"))
    monkeypatch.setenv("AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER", "1")

    provider = _CapturingProvider()
    import agentic_swmm.agent.runtime_loop as runtime_loop
    from agentic_swmm.agent.tool_registry import AgentToolRegistry

    monkeypatch.setattr(
        "agentic_swmm.agent.runtime_loop.OpenAIProvider", lambda *a, **kw: provider
    )
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
    session_dir = tmp_path / "2026-05-14" / "100000_empty_chat"
    session_dir.mkdir(parents=True)
    runtime_loop.run_openai_planner(
        args,
        goal="hi",
        session_dir=session_dir,
        trace_path=session_dir / "agent_trace.jsonl",
        registry=AgentToolRegistry(),
        chat_session=True,
    )
    first = provider.system_prompts[0]
    assert "<project-facts" not in first
