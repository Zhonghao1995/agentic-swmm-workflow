"""Phase F — ``generate_design_storm`` typed-tool handler.

The design-storm engine + ``aiswmm storm`` CLI + tests already existed, but
the planner could only reach storm generation via ``run_allowed_command``.
This exposes it as a first-class typed tool (consistent with the no-mode-gate
dispatch architecture), so the LLM can chain
``generate_design_storm -> build_inp -> run_swmm_inp``.

Mirrors ``test_tool_handlers_swmm_map``: validation gates + argv translation
(``_run_cli_tool`` mocked) + registry presence.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from agentic_swmm.agent.tool_handlers.swmm_storm import _generate_design_storm_tool
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


class GenerateDesignStormValidationTests(unittest.TestCase):
    def test_missing_shape_returns_failure(self) -> None:
        call = ToolCall(name="generate_design_storm", args={"out": "storm.dat"})
        result = _generate_design_storm_tool(call, Path("/tmp"))
        self.assertFalse(result["ok"])
        self.assertIn("shape", result["summary"])

    def test_missing_out_returns_failure(self) -> None:
        call = ToolCall(name="generate_design_storm", args={"shape": "chicago"})
        result = _generate_design_storm_tool(call, Path("/tmp"))
        self.assertFalse(result["ok"])
        self.assertIn("out", result["summary"])


class GenerateDesignStormArgvTests(unittest.TestCase):
    @mock.patch("agentic_swmm.agent.tool_handlers.swmm_storm._run_cli_tool")
    def test_minimal_call_builds_storm_argv(self, mock_run: mock.Mock) -> None:
        mock_run.return_value = {"tool": "generate_design_storm", "ok": True, "summary": "ok"}
        call = ToolCall(name="generate_design_storm", args={"shape": "uniform", "out": "s.dat"})
        _generate_design_storm_tool(call, Path("/tmp"))
        argv = mock_run.call_args.args[2]
        self.assertEqual(argv[0], "storm")
        self.assertIn("--shape", argv)
        self.assertIn("uniform", argv)
        self.assertIn("--out", argv)
        self.assertIn("s.dat", argv)

    @mock.patch("agentic_swmm.agent.tool_handlers.swmm_storm._run_cli_tool")
    def test_chicago_with_all_optionals(self, mock_run: mock.Mock) -> None:
        mock_run.return_value = {"ok": True, "summary": "ok"}
        call = ToolCall(
            name="generate_design_storm",
            args={
                "shape": "chicago",
                "out": "storm.dat",
                "depth_mm": 25,
                "duration_min": 60,
                "peak_position": 0.4,
            },
        )
        _generate_design_storm_tool(call, Path("/tmp"))
        argv = mock_run.call_args.args[2]
        self.assertEqual(argv[:3], ["storm", "--shape", "chicago"])
        self.assertIn("--depth-mm", argv)
        self.assertIn("25", argv)
        self.assertIn("--duration-min", argv)
        self.assertIn("60", argv)
        self.assertIn("--peak-position", argv)
        self.assertIn("0.4", argv)

    @mock.patch("agentic_swmm.agent.tool_handlers.swmm_storm._run_cli_tool")
    def test_bool_in_numeric_field_is_dropped(self, mock_run: mock.Mock) -> None:
        mock_run.return_value = {"ok": True, "summary": "ok"}
        call = ToolCall(
            name="generate_design_storm",
            args={"shape": "uniform", "out": "s.dat", "duration_min": True},
        )
        _generate_design_storm_tool(call, Path("/tmp"))
        argv = mock_run.call_args.args[2]
        self.assertNotIn("--duration-min", argv)


class GenerateDesignStormRegistryTests(unittest.TestCase):
    def test_tool_is_registered_and_not_read_only(self) -> None:
        registry = AgentToolRegistry()
        self.assertIn("generate_design_storm", registry.names)
        # It writes a .dat file, so it must prompt under QUICK (not read-only).
        self.assertFalse(registry.is_read_only("generate_design_storm"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
