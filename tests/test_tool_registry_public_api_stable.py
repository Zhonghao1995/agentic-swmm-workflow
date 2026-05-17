"""Public-API stability for ``agentic_swmm.agent.tool_registry`` (PRD #128).

The #128 split moves tool handlers into ``agent/tool_handlers/`` submodules.
This test asserts the documented public surface continues to be importable
from ``tool_registry``, so the refactor stays a pure no-behaviour-change
move and downstream callers do not need to update their imports.
"""

from __future__ import annotations

import unittest


class ToolRegistryPublicApiTests(unittest.TestCase):
    def test_agent_tool_registry_importable(self) -> None:
        from agentic_swmm.agent.tool_registry import AgentToolRegistry

        registry = AgentToolRegistry()
        self.assertIsInstance(registry.names, set)
        self.assertGreater(len(registry.names), 0)

    def test_tool_spec_importable(self) -> None:
        from agentic_swmm.agent.tool_registry import ToolSpec

        # ToolSpec is a frozen dataclass; verify its fields directly.
        fields = {f.name for f in ToolSpec.__dataclass_fields__.values()}
        self.assertIn("name", fields)
        self.assertIn("handler", fields)
        self.assertIn("parameters", fields)

    def test_compute_intent_signals_importable(self) -> None:
        from agentic_swmm.agent.tool_registry import compute_intent_signals

        signals = compute_intent_signals("plot the figure")
        self.assertIn("wants_plot", signals)
        self.assertTrue(signals["wants_plot"])

    def test_valid_mode_enum_importable(self) -> None:
        """``_VALID_MODE_ENUM`` is consumed by ``intent_disambiguator``."""
        from agentic_swmm.agent.tool_registry import _VALID_MODE_ENUM

        self.assertIsInstance(_VALID_MODE_ENUM, (set, frozenset, tuple, list))
        self.assertGreater(len(_VALID_MODE_ENUM), 0)


if __name__ == "__main__":
    unittest.main()
