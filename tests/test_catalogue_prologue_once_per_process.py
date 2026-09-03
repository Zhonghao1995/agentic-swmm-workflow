"""The catalogue prologue runs once per process (F-70).

Live finding 2026-09-02 (scenario S03 r2): the shell builds a fresh
registry every turn and an answer-continuation carries no prior state, so
list_skills, list_mcp_servers and one list_mcp_tools per relevant server
(about ten tool calls) ran again on the second modeling turn of a session.
Nothing in the catalogue can change inside one process.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentic_swmm.agent import planner as planner_mod
from agentic_swmm.agent.planner import Planner, reset_catalogue_memo
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall

LISTING = {"list_skills", "list_mcp_servers", "list_mcp_tools"}


class _RecordingExecutor:
    def __init__(self, trace_path: Path) -> None:
        self.results: list[dict[str, Any]] = []
        self.dry_run = False
        self.recorded: list[ToolCall] = []
        self.trace_path = trace_path

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        self.recorded.append(call)
        result = {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}
        self.results.append(result)
        return result


def _planner() -> Planner:
    return Planner(provider=None, registry=AgentToolRegistry(), max_steps=8, verbose=False, emit=lambda text: None)  # type: ignore[arg-type]


def _prologue(planner: Planner, executor: _RecordingExecutor, goal: str) -> list[str]:
    plan: list[ToolCall] = []
    planner._consult_workflow_skills(goal=goal, plan=plan, executor=executor, prior_session_state=None)
    return [c.name for c in executor.recorded]


@pytest.fixture
def memo_enabled(monkeypatch):
    monkeypatch.delenv(planner_mod.ALWAYS_INTROSPECT_ENV, raising=False)
    reset_catalogue_memo()
    yield
    reset_catalogue_memo()


def test_the_second_prologue_of_a_process_skips_the_listing(memo_enabled, tmp_path):
    trace = tmp_path / "t.jsonl"
    first = _prologue(_planner(), _RecordingExecutor(trace), "run examples/tecnopolo.inp")
    assert LISTING & set(first), first
    second = _prologue(_planner(), _RecordingExecutor(trace), "fetch the Esquimalt model and run it")
    assert not (LISTING & set(second)), second
    events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    assert any(e.get("event") == "prologue_skipped" for e in events)


def test_a_changed_catalogue_lists_again(memo_enabled, tmp_path, monkeypatch):
    trace = tmp_path / "t.jsonl"
    _prologue(_planner(), _RecordingExecutor(trace), "run examples/tecnopolo.inp")
    monkeypatch.setattr(planner_mod, "_catalogue_fingerprint", lambda: ("edited",))
    again = _prologue(_planner(), _RecordingExecutor(trace), "run examples/tecnopolo.inp")
    assert LISTING & set(again), again


def test_the_override_lists_every_turn(tmp_path, monkeypatch):
    monkeypatch.setenv(planner_mod.ALWAYS_INTROSPECT_ENV, "1")
    reset_catalogue_memo()
    trace = tmp_path / "t.jsonl"
    _prologue(_planner(), _RecordingExecutor(trace), "run examples/tecnopolo.inp")
    again = _prologue(_planner(), _RecordingExecutor(trace), "run examples/tecnopolo.inp")
    assert LISTING & set(again), again
