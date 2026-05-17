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
