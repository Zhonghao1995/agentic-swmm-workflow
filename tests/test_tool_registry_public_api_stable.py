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

    # LLM-driven dispatch refactor: ``_VALID_MODE_ENUM`` and the
    # ``intent_disambiguator`` consumer are gone, so the import-stability
    # contract no longer applies. ``compute_intent_signals`` was the last
    # legacy adapter; it was removed after its final callers migrated to
    # ``intent_classifier.classify_intent``.


if __name__ == "__main__":
    unittest.main()
