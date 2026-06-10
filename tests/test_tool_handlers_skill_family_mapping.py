"""Sanity-check the ``tool_handlers/`` skill-family layout (PRD #128).

This test asserts that every handler exported from ``tool_handlers``
keeps its public name, and that the registry's tool list includes the
tools that were migrated. It is the regression guard against future
slices forgetting to re-export a handler from ``tool_registry``.
"""

from __future__ import annotations

import importlib
import unittest

from agentic_swmm.agent.tool_registry import AgentToolRegistry


# Each entry: (submodule_name, expected_symbols, registry_tool_names).
# Add entries here as new skill families migrate out of tool_registry.py.
_MIGRATED_FAMILIES = [
    (
        "web",
        ("_web_fetch_url_tool", "_web_search_tool"),
        ("web_fetch_url", "web_search"),
    ),
    (
        "demo",
        ("_demo_acceptance_tool",),
        ("demo_acceptance",),
    ),
    (
        "swmm_memory",
        (
            "_recall_memory_tool",
            "_recall_memory_search_tool",
            "_recall_session_history_tool",
            "_record_fact_tool",
        ),
        (
            "recall_memory",
            "recall_memory_search",
            "recall_session_history",
            "record_fact",
        ),
    ),
    # PRD #128 Phase 2 Group B (network / climate / audit). See
    # ``tool_handlers/swmm_network.py``, ``swmm_climate.py``, ``swmm_audit.py``.
    (
        "swmm_network",
        (
            "_network_qa_args",
            "_network_qa_tool",
            "_network_to_inp_args",
            "_network_to_inp_tool",
        ),
        ("network_qa", "network_to_inp"),
    ),
    (
        "swmm_climate",
        ("_format_rainfall_args", "_format_rainfall_tool"),
        ("format_rainfall",),
    ),
    (
        "swmm_audit",
        ("_audit_run_args", "_audit_run_tool"),
        ("audit_run",),
    ),
    # PRD #128 Phase 2 Group A — runner / plot / builder.
    (
        "swmm_runner",
        ("_run_swmm_inp_args", "_run_swmm_inp_tool"),
        ("run_swmm_inp",),
    ),
    (
        "swmm_plot",
        ("_inspect_plot_options_tool", "_plot_run_args", "_plot_run_tool"),
        ("inspect_plot_options", "plot_run"),
    ),
    # Sibling of ``swmm_plot`` at the CLI verb level (``aiswmm map`` vs
    # ``aiswmm plot``). The LLM-facing tool stays in its own family file
    # rather than living inside ``swmm_plot`` so the network-layout
    # surface can evolve independently of the hydrograph surface.
    (
        "swmm_map",
        ("_map_run_tool",),
        ("map_run",),
    ),
    (
        "swmm_builder",
        ("_build_inp_args", "_build_inp_tool"),
        ("build_inp",),
    ),
    # In-process .rpt summary-section parser. Sits in its own family
    # because it has no CLI verb and no MCP server — it is a typed
    # surface over a stable file format (SWMM 5.2.4 rpt sections).
    (
        "swmm_rpt",
        ("_read_rpt_summary_tool",),
        ("read_rpt_summary",),
    ),
    # Thin CLI wrapper (``aiswmm storm``) exposing the design-storm engine
    # as a first-class typed tool so the LLM dispatches it directly instead
    # of via run_allowed_command.
    (
        "swmm_storm",
        ("_generate_design_storm_tool",),
        ("generate_storm_shape",),
    ),
    # New-case onboarding rewire (#246 follow-up): apply_onboarding tool.
    (
        "swmm_onboarding",
        ("_apply_onboarding_tool",),
        ("apply_onboarding",),
    ),
    # dark-MCP registration (PR 1, issue #246): 6 calibration tools registered
    # as first-class typed ToolSpecs.
    (
        "swmm_calibration",
        (
            "_swmm_calibrate_common_schema",
            "_sensitivity_scan_args",
            "_calibrate_args",
            "_calibrate_search_args",
            "_calibrate_sceua_args",
            "_calibrate_dream_zs_args",
            "_validate_args",
            "_swmm_sensitivity_scan_tool",
            "_swmm_calibrate_tool",
            "_swmm_calibrate_search_tool",
            "_swmm_calibrate_sceua_tool",
            "_swmm_calibrate_dream_zs_tool",
            "_swmm_validate_tool",
        ),
        (
            "swmm_sensitivity_scan",
            "swmm_calibrate",
            "swmm_calibrate_search",
            "swmm_calibrate_sceua",
            "swmm_calibrate_dream_zs",
            "swmm_validate",
        ),
    ),
]


class ToolHandlersSkillFamilyTests(unittest.TestCase):
    def test_submodules_export_expected_symbols(self) -> None:
        for module_name, symbols, _ in _MIGRATED_FAMILIES:
            module = importlib.import_module(
                f"agentic_swmm.agent.tool_handlers.{module_name}"
            )
            for symbol in symbols:
                with self.subTest(module=module_name, symbol=symbol):
                    self.assertTrue(
                        hasattr(module, symbol),
                        f"{module_name}.{symbol} missing — re-export drift?",
                    )

    def test_tool_registry_still_registers_migrated_tools(self) -> None:
        registry = AgentToolRegistry()
        for _, _, tool_names in _MIGRATED_FAMILIES:
            for tool_name in tool_names:
                with self.subTest(tool=tool_name):
                    self.assertIn(
                        tool_name,
                        registry.names,
                        f"{tool_name} was migrated to tool_handlers/ but is no "
                        "longer registered — _build_tools() missed an import.",
                    )

    def test_tool_registry_reexports_migrated_handlers(self) -> None:
        """``tool_registry`` keeps the private ``_*_tool`` import paths so
        existing test fixtures and callers do not need to migrate."""
        from agentic_swmm.agent import tool_registry

        for _, symbols, _ in _MIGRATED_FAMILIES:
            for symbol in symbols:
                with self.subTest(symbol=symbol):
                    self.assertTrue(
                        hasattr(tool_registry, symbol),
                        f"tool_registry.{symbol} re-export missing.",
                    )


if __name__ == "__main__":
    unittest.main()
