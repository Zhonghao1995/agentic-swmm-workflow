"""F-50 (2026-09-02): a tool is always reachable by naming its skill.

Offline check of the campaign's remaining prompts against
``select_relevant_skills`` showed "How uncertain is the peak outflow... vary
Manning's n... tell me the spread" picking no swmm-uncertainty (the keywords
were uncertainty/fuzzy/alpha-cut/membership). With the goal-scoped schema
subset (F-44) that skill's tools were unreachable for the whole turn.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_swmm.agent.intent_classifier import select_relevant_skills
from agentic_swmm.agent.planner import TOOL_SUBSET_ENV, Planner
from agentic_swmm.agent.skill_router import SkillRouter
from agentic_swmm.agent.tool_registry import AgentToolRegistry


@pytest.mark.parametrize(
    "goal",
    [
        "How uncertain is the peak outflow of that run? Vary Manning's n and imperviousness and tell me the spread.",
        "Run a sensitivity analysis on the imperviousness of that model.",
        "Give me a Monte Carlo spread of peak flow for the downtown model.",
        "这个模型的峰值流量敏感性怎么样？",
    ],
)
def test_common_uncertainty_phrasings_select_the_skill(goal):
    assert "swmm-uncertainty" in select_relevant_skills(goal)


def _planner(monkeypatch):
    monkeypatch.setenv(TOOL_SUBSET_ENV, "1")
    planner = Planner.__new__(Planner)
    planner.registry = AgentToolRegistry()
    return planner


def test_selecting_a_skill_mid_turn_adds_its_tools(monkeypatch, tmp_path):
    planner = _planner(monkeypatch)
    trace = tmp_path / "agent_trace.jsonl"
    subset = planner._tool_subset_for("Run the model in that folder and audit it.", trace_path=trace)
    assert subset is not None
    uncertainty = set(SkillRouter(planner.registry).tools_for("swmm-uncertainty").tool_names())
    assert not uncertainty <= subset, "the goal's keywords must not already cover the skill"

    grown = planner._grow_tool_subset(subset, "swmm-uncertainty", trace_path=trace)

    assert uncertainty <= grown
    assert subset <= grown
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    grown_events = [e for e in events if e.get("event") == "tool_subset_grown"]
    assert grown_events and grown_events[0]["skill"] == "swmm-uncertainty"
    assert set(grown_events[0]["added"]) == uncertainty - subset


def test_growing_an_inactive_subset_stays_inactive(monkeypatch, tmp_path):
    planner = _planner(monkeypatch)
    assert planner._grow_tool_subset(None, "swmm-uncertainty", trace_path=tmp_path / "t.jsonl") is None


def test_an_unknown_skill_leaves_the_subset_alone(monkeypatch, tmp_path):
    planner = _planner(monkeypatch)
    subset = {"list_skills", "select_skill"}
    assert planner._grow_tool_subset(set(subset), "no-such-skill", trace_path=tmp_path / "t.jsonl") == subset


def test_the_schemas_follow_the_grown_subset(monkeypatch, tmp_path):
    planner = _planner(monkeypatch)
    subset = planner._tool_subset_for("Run the model in that folder and audit it.", trace_path=None)
    grown = planner._grow_tool_subset(subset, "swmm-uncertainty", trace_path=None)
    names = {schema["name"] for schema in planner.registry.schemas(grown)}
    assert names == grown
