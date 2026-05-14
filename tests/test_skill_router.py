"""Unit tests for ``agentic_swmm.agent.skill_router.SkillRouter`` (PRD-Y).

PRD-Y "Module: SkillRouter" decision contract:

- ``list_skills`` exposes the configured skill set including the
  virtual ``agent-internal`` bucket.
- ``tools_for("swmm-builder")`` returns the deterministic-SWMM tool
  subset for that skill — ``build_inp`` here.
- ``tools_for("agent-internal")`` returns the in-process subset
  (memory / workflow / inspect / recall / read / list etc.).
- An unknown skill name raises ``KeyError`` so callers can wrap it as
  a fail-soft tool result.
"""

from __future__ import annotations

import pytest

from agentic_swmm.agent.skill_router import (
    AGENT_INTERNAL_SKILL,
    SkillRouter,
    SkillTools,
)
from agentic_swmm.agent.tool_registry import AgentToolRegistry


@pytest.fixture
def router() -> SkillRouter:
    return SkillRouter(AgentToolRegistry())


def test_list_skills_includes_agent_internal_and_each_mcp_skill(router: SkillRouter) -> None:
    skills = router.list_skills()
    # Always-available virtual skill.
    assert AGENT_INTERNAL_SKILL in skills
    # Every deterministic-SWMM skill named in the PRD coverage matrix.
    for expected in (
        "swmm-builder",
        "swmm-climate",
        "swmm-experiment-audit",
        "swmm-modeling-memory",
        "swmm-network",
        "swmm-plot",
        "swmm-runner",
    ):
        assert expected in skills, f"{expected} missing from {skills}"


def test_tools_for_swmm_builder_returns_build_inp(router: SkillRouter) -> None:
    bundle = router.tools_for("swmm-builder")
    assert isinstance(bundle, SkillTools)
    assert bundle.source == "mcp"
    names = bundle.tool_names()
    assert names == ["build_inp"], f"expected only build_inp in swmm-builder; got {names}"


def test_tools_for_swmm_network_returns_qa_and_export(router: SkillRouter) -> None:
    bundle = router.tools_for("swmm-network")
    names = set(bundle.tool_names())
    assert names == {"network_qa", "network_to_inp"}


def test_agent_internal_skill_includes_memory_and_introspection(
    router: SkillRouter,
) -> None:
    bundle = router.tools_for(AGENT_INTERNAL_SKILL)
    assert bundle.source == "in-process"
    names = set(bundle.tool_names())
    # PRD-Y: agent-internal includes memory recall, workflow selection,
    # plot option inspection, skill / file / dir / git / web / mcp meta
    # tools — anything that is NOT a deterministic-SWMM operation.
    for tool in (
        "recall_memory",
        "recall_memory_search",
        "recall_session_history",
        "record_fact",
        "select_workflow_mode",
        "inspect_plot_options",
        "list_skills",
        "read_skill",
        "read_file",
        "search_files",
        "list_dir",
        "git_diff",
        "web_fetch_url",
        "web_search",
        "capabilities",
        "doctor",
        "list_mcp_servers",
        "list_mcp_tools",
        "call_mcp_tool",
        "apply_patch",
        "run_tests",
        "run_allowed_command",
        "demo_acceptance",
    ):
        assert tool in names, f"{tool} missing from agent-internal subset"


def test_agent_internal_does_not_contain_deterministic_swmm_tools(
    router: SkillRouter,
) -> None:
    bundle = router.tools_for(AGENT_INTERNAL_SKILL)
    names = set(bundle.tool_names())
    # The deterministic-SWMM operations must live in their MCP skill,
    # not in the agent-internal bucket — otherwise the planner could
    # invoke them without first committing to a skill.
    for forbidden in (
        "build_inp",
        "format_rainfall",
        "network_qa",
        "network_to_inp",
        "plot_run",
        "run_swmm_inp",
        "audit_run",
        "summarize_memory",
    ):
        assert forbidden not in names, (
            f"{forbidden} must live under its own skill, not agent-internal"
        )


def test_tools_for_unknown_skill_raises_keyerror(router: SkillRouter) -> None:
    with pytest.raises(KeyError):
        router.tools_for("not-a-real-skill")


def test_skill_tools_schemas_are_json_serialisable(router: SkillRouter) -> None:
    import json

    bundle = router.tools_for("swmm-builder")
    schemas = bundle.schemas()
    assert schemas
    # ``schemas`` must round-trip through json — that's what the
    # ``select_skill`` handler will surface to the planner.
    json.dumps(schemas)


def test_virtual_agent_internal_skill_helper(router: SkillRouter) -> None:
    bundle = router.virtual_agent_internal_skill()
    assert bundle.skill_name == AGENT_INTERNAL_SKILL
    assert bundle.source == "in-process"
