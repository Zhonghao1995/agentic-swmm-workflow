"""Tests for the new ``select_skill`` ToolSpec (PRD-Y).

PRD-Y "Two-level planner surface":

- ``select_skill(skill_name)`` is read-only — it returns the skill's
  tool subset without performing any deterministic-SWMM operation.
- The description carries the literal ``USE WHEN`` / ``DO NOT USE WHEN``
  routing text required by the planner-routing convention (mirrors
  ``recall_memory`` / ``recall_memory_search``).
- A valid skill_name returns ``ok=True`` with a JSON-serialisable tool
  list (name + description + parameters).
- An invalid skill_name returns ``ok=False`` with a clear summary so
  the planner fail-soft loop can react.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentic_swmm.agent.skill_router import AGENT_INTERNAL_SKILL
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


def test_select_skill_is_registered_and_read_only() -> None:
    registry = AgentToolRegistry()
    assert "select_skill" in registry.names
    assert registry.is_read_only("select_skill") is True


def test_select_skill_description_carries_routing_phrases() -> None:
    registry = AgentToolRegistry()
    schemas = {schema["name"]: schema for schema in registry.schemas()}
    desc = schemas["select_skill"]["description"]
    assert "USE WHEN" in desc
    assert "DO NOT USE WHEN" in desc


def test_select_skill_with_valid_skill_returns_tool_list(tmp_path: Path) -> None:
    registry = AgentToolRegistry()
    result = registry.execute(
        ToolCall("select_skill", {"skill_name": "swmm-builder"}),
        session_dir=tmp_path,
    )
    assert result["ok"] is True
    assert result["tool"] == "select_skill"
    # The handler exposes the skill's tools as a list under ``tools`` so
    # the planner can read it and pick the next concrete tool.
    tools = result.get("tools")
    assert isinstance(tools, list) and tools
    names = {entry["name"] for entry in tools if isinstance(entry, dict)}
    assert "build_inp" in names
    # Each entry carries enough for the planner to compose the call.
    for entry in tools:
        assert "name" in entry and "description" in entry and "parameters" in entry
    # Round-trip through JSON — the planner serialises this as the
    # function_call_output payload, so it must be JSON-safe.
    json.dumps(tools)
    # The skill name is echoed back for the planner's bookkeeping.
    assert result.get("skill_name") == "swmm-builder"


def test_select_skill_with_agent_internal_returns_in_process_subset(tmp_path: Path) -> None:
    registry = AgentToolRegistry()
    result = registry.execute(
        ToolCall("select_skill", {"skill_name": AGENT_INTERNAL_SKILL}),
        session_dir=tmp_path,
    )
    assert result["ok"] is True
    names = {entry["name"] for entry in result["tools"]}
    assert "recall_memory" in names
    assert "select_workflow_mode" in names
    # The agent-internal subset must NOT carry deterministic-SWMM tools.
    assert "build_inp" not in names


def test_select_skill_with_unknown_skill_returns_failure(tmp_path: Path) -> None:
    registry = AgentToolRegistry()
    result = registry.execute(
        ToolCall("select_skill", {"skill_name": "not-a-real-skill"}),
        session_dir=tmp_path,
    )
    assert result["ok"] is False
    assert "unknown skill" in result["summary"].lower()


def test_select_skill_with_missing_argument_returns_failure(tmp_path: Path) -> None:
    registry = AgentToolRegistry()
    result = registry.execute(
        ToolCall("select_skill", {}),
        session_dir=tmp_path,
    )
    assert result["ok"] is False
    assert "skill_name" in result["summary"].lower()
