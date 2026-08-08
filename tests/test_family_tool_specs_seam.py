"""The family self-registration seam (issue #358 PR B)."""
from __future__ import annotations

import sys
import types as module_types

import pytest

from agentic_swmm.agent import tool_registry
from agentic_swmm.agent.types import ToolSpec


def test_pilot_families_register_through_the_seam():
    registry = tool_registry.AgentToolRegistry()
    assert "fetch_swmm_from_canada" in registry.names
    assert "run_climate_scenarios" in registry.names

    from agentic_swmm.agent.tool_handlers.swmm_canada import fetch_swmm_from_canada_tool
    from agentic_swmm.agent.tool_handlers.swmm_climate import run_climate_scenarios_tool

    tools = tool_registry._build_tools()
    assert tools["fetch_swmm_from_canada"].handler is fetch_swmm_from_canada_tool
    assert tools["run_climate_scenarios"].handler is run_climate_scenarios_tool
    # Both write files; the seam must not accidentally grant read-only.
    assert tools["fetch_swmm_from_canada"].is_read_only is False
    assert tools["run_climate_scenarios"].is_read_only is False


def test_family_specs_come_from_the_family_modules():
    specs = {spec.name for spec in tool_registry._family_specs()}
    assert specs == {
        "fetch_swmm_from_canada",
        "run_climate_scenarios",
        "map_run",
        "inspect_plot_options",
        "plot_run",
        "read_rpt_summary",
        "run_swmm_inp",
        "apply_onboarding",
        "build_inp",
        "network_qa",
        "network_to_inp",
        "synth_swmm_from_bbox",
        "swmm_sensitivity_scan",
        "swmm_calibrate",
        "swmm_calibrate_search",
        "swmm_calibrate_sceua",
        "swmm_calibrate_dream_zs",
        "swmm_validate",
        "swmm_sensitivity_oat",
        "swmm_sensitivity_morris",
        "swmm_sensitivity_sobol",
        "swmm_rainfall_ensemble",
        "swmm_uncertainty_source_decomposition",
        "web_fetch_url",
        "web_search",
        "audit_run",
        "apply_patch",
        "build_raingage_section",
        "format_rainfall",
        "generate_design_storm",
        "generate_storm_shape",
        "demo_acceptance",
        "doctor",
        "retrieve_memory",
        "git_diff",
        "list_dir",
        "list_skills",
        "read_file",
        "read_skill",
        "search_files",
        "recall_memory",
        "recall_memory_search",
        "recall_session_history",
        "record_fact",
        "request_expert_review",
        "request_gap_judgement",
        "generate_report",
        "review_run",
        "read_wq_loads",
    }


def test_c1_families_keep_their_read_only_grants():
    tools = tool_registry._build_tools()
    assert tools["inspect_plot_options"].is_read_only is True
    assert tools["read_rpt_summary"].is_read_only is True
    assert tools["plot_run"].is_read_only is False
    assert tools["map_run"].is_read_only is False
    assert tools["run_swmm_inp"].is_read_only is False


def test_duplicate_family_tool_name_fails_loudly(monkeypatch):
    fake = module_types.ModuleType("aiswmm_fake_family")

    def tool_specs() -> list[ToolSpec]:
        return [
            ToolSpec(
                "run_swmm_inp",  # collides with a grouped-builder tool
                "fake duplicate",
                {"type": "object", "properties": {}},
                lambda call, session_dir: {"ok": True},
            )
        ]

    fake.tool_specs = tool_specs
    monkeypatch.setitem(sys.modules, "aiswmm_fake_family", fake)
    monkeypatch.setattr(
        tool_registry,
        "_FAMILY_SPEC_MODULES",
        tool_registry._FAMILY_SPEC_MODULES + ("aiswmm_fake_family",),
    )
    with pytest.raises(ValueError, match="family seam duplicate"):
        tool_registry._build_tools()
