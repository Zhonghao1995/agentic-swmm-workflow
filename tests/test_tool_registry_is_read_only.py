"""Lock the read-only classification of every tool in ``_build_tools()``.

PRD_runtime adds ``ToolSpec.is_read_only`` (default ``False`` — fail-safe)
and a ``registry.is_read_only(name)`` method. This test pins the set
of tools that are classified read-only so that future additions
(e.g., Memory PRD's ``recall_memory`` / ``recall_memory_search``)
have to make a deliberate choice rather than inheriting the default.
"""
from __future__ import annotations

import unittest

from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolSpec


# Per PRD_runtime "Module: ``is_read_only`` metadata on ``ToolSpec``":
# read-only (True) covers ``read_file``, ``list_*``, ``search_files``,
# ``git_diff``, ``web_*``, ``inspect_plot_options``, ``read_skill``,
# ``list_skills``, ``list_mcp_servers``, ``list_mcp_tools``.
# PRD-Y adds ``select_skill`` — it only returns the skill's tool subset.
# #79 P1-5 adds ``capabilities`` and ``select_workflow_mode`` — both pure
# read/inspect tools that were drifting on the False default.
EXPECTED_READ_ONLY: set[str] = {
    "capabilities",
    "git_diff",
    "inspect_plot_options",
    "list_dir",
    "list_mcp_servers",
    "list_mcp_tools",
    "list_skills",
    "read_file",
    "read_skill",
    "recall_memory",
    "recall_memory_search",
    "recall_session_history",
    "search_files",
    "select_skill",
    "select_workflow_mode",
    "web_fetch_url",
    "web_search",
}


class ToolSpecHasIsReadOnlyTests(unittest.TestCase):
    def test_dataclass_default_is_false(self) -> None:
        """Adding ``is_read_only`` must be fail-safe: default ``False``."""

        def _handler(call, session_dir):  # pragma: no cover - inert
            return {"tool": call.name, "args": call.args, "ok": True}

        spec = ToolSpec(
            name="probe",
            description="probe tool",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=_handler,
        )
        self.assertFalse(spec.is_read_only)

    def test_dataclass_can_be_constructed_with_is_read_only_true(self) -> None:
        def _handler(call, session_dir):  # pragma: no cover - inert
            return {"tool": call.name, "args": call.args, "ok": True}

        spec = ToolSpec(
            name="probe",
            description="probe tool",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=_handler,
            is_read_only=True,
        )
        self.assertTrue(spec.is_read_only)


class RegistryReadOnlyClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AgentToolRegistry()

    def test_read_file_is_read_only(self) -> None:
        self.assertTrue(self.registry.is_read_only("read_file"))

    def test_plot_run_is_not_read_only(self) -> None:
        self.assertFalse(self.registry.is_read_only("plot_run"))

    def test_unknown_tool_is_not_read_only(self) -> None:
        self.assertFalse(self.registry.is_read_only("definitely-not-a-real-tool"))

    def test_read_only_set_matches_expected(self) -> None:
        actual = {name for name in self.registry.names if self.registry.is_read_only(name)}
        self.assertEqual(
            actual,
            EXPECTED_READ_ONLY,
            f"read-only classification drifted: "
            f"unexpected={sorted(actual - EXPECTED_READ_ONLY)} "
            f"missing={sorted(EXPECTED_READ_ONLY - actual)}",
        )

    def test_every_tool_has_a_classification(self) -> None:
        # is_read_only must return a bool for every registered tool.
        for name in self.registry.names:
            value = self.registry.is_read_only(name)
            self.assertIsInstance(value, bool, f"{name} returned non-bool")


if __name__ == "__main__":
    unittest.main()
