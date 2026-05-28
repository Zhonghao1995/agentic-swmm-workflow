"""Unit tests for the ``map_run`` typed-tool handler.

The handler is the LLM-facing surface for ``aiswmm map`` — render the
spatial layout (subcatchments + network + outfalls) of a SWMM model.
It is a thin convenience wrapper around the existing CLI verb so the
LLM can chain ``synth_swmm_from_bbox -> run_swmm_inp -> map_run`` in a
single conversation without resorting to ``run_allowed_command`` and
fishing for the right ``python -m`` invocation.

These tests pin:

* the typed-param validation (missing / empty ``run_dir`` -> fail-soft
  ``_failure(...)`` payload, not an exception into the planner loop);
* the argv translation (every typed param maps to the matching
  ``aiswmm map`` flag, optional params are omitted when not provided);
* the registry plumbing (``map_run`` appears in
  :class:`AgentToolRegistry`).

The handler delegates to ``_run_cli_tool``, which is already covered
by ``tests/test_tool_handlers_shared_helpers.py``. We mock it here so
the unit tests stay fast and do not depend on matplotlib.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from agentic_swmm.agent.tool_handlers.swmm_map import _map_run_tool
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


class MapRunValidationTests(unittest.TestCase):
    """Typed-param validation gates the LLM-facing surface."""

    def test_missing_run_dir_returns_failure(self) -> None:
        call = ToolCall(name="map_run", args={})
        result = _map_run_tool(call, Path("/tmp"))
        self.assertFalse(result["ok"])
        self.assertIn("run_dir", result["summary"])

    def test_empty_run_dir_returns_failure(self) -> None:
        call = ToolCall(name="map_run", args={"run_dir": "   "})
        result = _map_run_tool(call, Path("/tmp"))
        self.assertFalse(result["ok"])
        self.assertIn("run_dir", result["summary"])

    def test_non_string_run_dir_returns_failure(self) -> None:
        call = ToolCall(name="map_run", args={"run_dir": 42})
        result = _map_run_tool(call, Path("/tmp"))
        self.assertFalse(result["ok"])
        self.assertIn("run_dir", result["summary"])


class MapRunArgvTranslationTests(unittest.TestCase):
    """Each typed argument maps to a specific ``aiswmm map`` CLI flag.

    We mock ``_run_cli_tool`` and inspect the constructed argv to lock
    in the translation. The CLI verb itself is covered by
    ``tests/test_map_command.py``.
    """

    @mock.patch("agentic_swmm.agent.tool_handlers.swmm_map._run_cli_tool")
    def test_minimal_call_translates_to_map_run_dir(self, mock_run: mock.Mock) -> None:
        mock_run.return_value = {"tool": "map_run", "ok": True, "summary": "ok"}
        call = ToolCall(name="map_run", args={"run_dir": "runs/agent/sample"})
        _map_run_tool(call, Path("/tmp"))
        argv = mock_run.call_args.args[2]
        self.assertEqual(argv, ["map", "--run-dir", "runs/agent/sample"])

    @mock.patch("agentic_swmm.agent.tool_handlers.swmm_map._run_cli_tool")
    def test_inp_override_appends_inp_flag(self, mock_run: mock.Mock) -> None:
        mock_run.return_value = {"ok": True, "summary": "ok"}
        call = ToolCall(
            name="map_run",
            args={"run_dir": "runs/agent/sample", "inp": "examples/foo.inp"},
        )
        _map_run_tool(call, Path("/tmp"))
        argv = mock_run.call_args.args[2]
        self.assertIn("--inp", argv)
        self.assertEqual(argv[argv.index("--inp") + 1], "examples/foo.inp")

    @mock.patch("agentic_swmm.agent.tool_handlers.swmm_map._run_cli_tool")
    def test_out_png_appends_flag(self, mock_run: mock.Mock) -> None:
        mock_run.return_value = {"ok": True, "summary": "ok"}
        call = ToolCall(
            name="map_run",
            args={"run_dir": "runs/agent/sample", "out_png": "runs/agent/sample/m.png"},
        )
        _map_run_tool(call, Path("/tmp"))
        argv = mock_run.call_args.args[2]
        self.assertIn("--out-png", argv)
        self.assertEqual(
            argv[argv.index("--out-png") + 1], "runs/agent/sample/m.png"
        )

    @mock.patch("agentic_swmm.agent.tool_handlers.swmm_map._run_cli_tool")
    def test_dpi_appends_flag(self, mock_run: mock.Mock) -> None:
        mock_run.return_value = {"ok": True, "summary": "ok"}
        call = ToolCall(name="map_run", args={"run_dir": "runs/agent/sample", "dpi": 300})
        _map_run_tool(call, Path("/tmp"))
        argv = mock_run.call_args.args[2]
        self.assertIn("--dpi", argv)
        self.assertEqual(argv[argv.index("--dpi") + 1], "300")

    @mock.patch("agentic_swmm.agent.tool_handlers.swmm_map._run_cli_tool")
    def test_invalid_dpi_is_silently_dropped(self, mock_run: mock.Mock) -> None:
        """A bad dpi (string, negative, zero) should not poison the argv;
        the CLI verb's argparse will then use its own default."""
        mock_run.return_value = {"ok": True, "summary": "ok"}
        for bad in ("hello", 0, -1, True):  # True is bool — never an int dpi
            with self.subTest(dpi=bad):
                call = ToolCall(
                    name="map_run",
                    args={"run_dir": "runs/agent/sample", "dpi": bad},
                )
                _map_run_tool(call, Path("/tmp"))
                argv = mock_run.call_args.args[2]
                self.assertNotIn("--dpi", argv)

    @mock.patch("agentic_swmm.agent.tool_handlers.swmm_map._run_cli_tool")
    def test_no_subcatchments_appends_flag_when_truthy(self, mock_run: mock.Mock) -> None:
        mock_run.return_value = {"ok": True, "summary": "ok"}
        call = ToolCall(
            name="map_run",
            args={"run_dir": "runs/agent/sample", "no_subcatchments": True},
        )
        _map_run_tool(call, Path("/tmp"))
        argv = mock_run.call_args.args[2]
        self.assertIn("--no-subcatchments", argv)

    @mock.patch("agentic_swmm.agent.tool_handlers.swmm_map._run_cli_tool")
    def test_no_vertices_appends_flag_when_truthy(self, mock_run: mock.Mock) -> None:
        mock_run.return_value = {"ok": True, "summary": "ok"}
        call = ToolCall(
            name="map_run",
            args={"run_dir": "runs/agent/sample", "no_vertices": True},
        )
        _map_run_tool(call, Path("/tmp"))
        argv = mock_run.call_args.args[2]
        self.assertIn("--no-vertices", argv)

    @mock.patch("agentic_swmm.agent.tool_handlers.swmm_map._run_cli_tool")
    def test_falsey_bool_flags_are_omitted(self, mock_run: mock.Mock) -> None:
        mock_run.return_value = {"ok": True, "summary": "ok"}
        call = ToolCall(
            name="map_run",
            args={
                "run_dir": "runs/agent/sample",
                "no_subcatchments": False,
                "no_vertices": False,
            },
        )
        _map_run_tool(call, Path("/tmp"))
        argv = mock_run.call_args.args[2]
        self.assertNotIn("--no-subcatchments", argv)
        self.assertNotIn("--no-vertices", argv)


class MapRunRegistryWiringTests(unittest.TestCase):
    """The typed tool must be visible to the LLM through the registry."""

    def test_map_run_is_registered(self) -> None:
        registry = AgentToolRegistry()
        self.assertIn("map_run", registry.names)

    def test_map_run_schema_requires_run_dir(self) -> None:
        registry = AgentToolRegistry()
        schemas = registry.schemas()
        spec = next(s for s in schemas if s["name"] == "map_run")
        self.assertIn("run_dir", spec["parameters"]["required"])

    def test_map_run_description_mentions_layout(self) -> None:
        """The description is what the LLM reads when picking tools.
        It must clearly distinguish ``map_run`` (spatial network map)
        from ``plot_run`` (rainfall-runoff hydrograph) so the LLM
        doesn't conflate the two."""
        registry = AgentToolRegistry()
        desc = registry.describe("map_run") or ""
        lower = desc.lower()
        self.assertTrue(
            "network" in lower or "layout" in lower or "map" in lower,
            f"description must signal spatial-map semantics; got: {desc!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
