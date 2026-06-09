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
        "swmm-calibration",
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
    # PRD-Y + LLM-driven dispatch refactor: agent-internal includes
    # memory recall, plot option inspection, skill / file / dir / git /
    # web / mcp meta tools — anything that is NOT a deterministic-SWMM
    # operation. The legacy ``select_workflow_mode`` gate is gone.
    for tool in (
        "recall_memory",
        "recall_memory_search",
        "recall_session_history",
        "record_fact",
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
        "audit_run",
        # C1 (issue #246): build_raingage_section → swmm-climate
        "build_raingage_section",
        "build_inp",
        "format_rainfall",
        "network_qa",
        "network_to_inp",
        "plot_run",
        # C5 (issue #246): retrieve_memory → swmm-rag-memory
        "retrieve_memory",
        "run_swmm_inp",
        "summarize_memory",
        # calibration tools (PR 1, issue #246)
        "swmm_calibrate",
        "swmm_calibrate_dream_zs",
        "swmm_calibrate_search",
        "swmm_calibrate_sceua",
        "swmm_sensitivity_scan",
        "swmm_validate",
        # uncertainty tools (PR 2, issue #246)
        "swmm_rainfall_ensemble",
        "swmm_sensitivity_morris",
        "swmm_sensitivity_oat",
        "swmm_sensitivity_sobol",
        "swmm_uncertainty_source_decomposition",
    ):
        assert forbidden not in names, (
            f"{forbidden} must live under its own skill, not agent-internal"
        )


def test_tools_for_swmm_calibration_returns_all_six_tools(router: SkillRouter) -> None:
    """dark-MCP PR 1: all 6 calibration ToolSpecs must map to swmm-calibration."""
    bundle = router.tools_for("swmm-calibration")
    assert isinstance(bundle, SkillTools)
    assert bundle.source == "mcp"
    names = set(bundle.tool_names())
    expected = {
        "swmm_calibrate",
        "swmm_calibrate_dream_zs",
        "swmm_calibrate_search",
        "swmm_calibrate_sceua",
        "swmm_sensitivity_scan",
        "swmm_validate",
    }
    assert names == expected, f"expected calibration tools {expected}; got {names}"


def test_tools_for_swmm_uncertainty_returns_all_five_tools(router: SkillRouter) -> None:
    """dark-MCP PR 2: all 5 uncertainty ToolSpecs must map to swmm-uncertainty."""
    bundle = router.tools_for("swmm-uncertainty")
    assert isinstance(bundle, SkillTools)
    assert bundle.source == "mcp"
    names = set(bundle.tool_names())
    expected = {
        "swmm_rainfall_ensemble",
        "swmm_sensitivity_morris",
        "swmm_sensitivity_oat",
        "swmm_sensitivity_sobol",
        "swmm_uncertainty_source_decomposition",
    }
    assert names == expected, f"expected uncertainty tools {expected}; got {names}"


def test_tools_for_swmm_climate_contains_both_tools(router: SkillRouter) -> None:
    """C1 (issue #246): build_raingage_section must join format_rainfall in swmm-climate."""
    bundle = router.tools_for("swmm-climate")
    names = set(bundle.tool_names())
    assert "format_rainfall" in names
    assert "build_raingage_section" in names, (
        "build_raingage_section must be bound to swmm-climate in _DETERMINISTIC_BINDINGS"
    )


def test_tools_for_swmm_rag_memory_contains_retrieve_memory(router: SkillRouter) -> None:
    """C5 (issue #246): retrieve_memory must be bound to swmm-rag-memory."""
    bundle = router.tools_for("swmm-rag-memory")
    assert "retrieve_memory" in bundle.tool_names(), (
        "retrieve_memory must be bound to swmm-rag-memory in _DETERMINISTIC_BINDINGS"
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
