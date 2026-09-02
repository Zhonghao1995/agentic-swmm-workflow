"""Goal-scoped tool schemas behind AISWMM_TOOL_SUBSET (F-44, 2026-09-02).

All 57 tool schemas (64k characters) went out on every LLM call and were most
of a Canada chain turn's input tokens; the primed SKILL.md reads never reached
the model at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.planner import Planner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from tests.test_onboarding_wiring import _ScriptedProvider

GOAL = ("Fetch a SWMM model from the Canada service for downtown Victoria BC, rainfall period "
        "November 1 to November 4 2023. Run the model and audit it.")


def _planner() -> Planner:
    return Planner(provider=_ScriptedProvider(), registry=AgentToolRegistry(), max_steps=2, verbose=False, emit=lambda t: None)  # type: ignore[arg-type]


class TestRegistrySchemas:
    def test_a_subset_is_honoured_and_unknown_names_ignored(self):
        registry = AgentToolRegistry()
        full = registry.schemas()
        some = registry.schemas({"read_rpt_summary", "fetch_swmm_from_canada", "no_such_tool"})
        assert [s["name"] for s in some] == ["fetch_swmm_from_canada", "read_rpt_summary"]
        assert len(full) > len(some)
        assert registry.schemas(None) == full


class TestPlannerSubset:
    def test_off_by_default_means_every_tool(self, monkeypatch):
        monkeypatch.delenv("AISWMM_TOOL_SUBSET", raising=False)
        assert _planner()._tool_subset_for(GOAL) is None

    def test_on_means_the_goal_skills_tools_plus_agent_internal(self, monkeypatch):
        monkeypatch.setenv("AISWMM_TOOL_SUBSET", "1")
        planner = _planner()
        with TemporaryDirectory() as raw:
            trace = Path(raw) / "t.jsonl"
            subset = planner._tool_subset_for(GOAL, trace_path=trace)
            events = [json.loads(l) for l in trace.read_text().splitlines() if l.strip()]
        assert subset is not None
        for needed in ("fetch_swmm_from_canada", "run_swmm_inp", "audit_run", "read_rpt_summary", "select_skill", "read_file"):
            assert needed in subset, needed
        assert "swmm_calibrate_dream_zs" not in subset
        assert len(subset) < len(planner.registry.names)
        assert events and events[0]["event"] == "tool_subset" and events[0]["tool_count"] == len(subset)
        payload = json.dumps(planner.registry.schemas(subset))
        assert len(payload) < 0.7 * len(json.dumps(planner.registry.schemas()))
