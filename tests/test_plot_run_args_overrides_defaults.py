"""Issue #125 invariant: the agent-driven plot flow ALWAYS passes
explicit ``rain_ts`` / ``node`` / ``node_attr`` to the MCP server. The
script + server defaults (``TS_RAIN`` / ``O1`` / ``Total_inflow``) are
unreachable in this path — they exist only for manual CLI invocation.

This regression test locks in the override: if a future refactor of
``_plot_run_args`` ever drops one of these fields from the MCP payload,
this test fails loudly so silent regressions to Tod-Creek-shaped
defaults are caught at CI time.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agentic_swmm.agent import tool_registry
from agentic_swmm.agent.tool_registry import _plot_run_args
from agentic_swmm.agent.types import ToolCall


def _seed_run_dir(root: Path, name: str = "test-run") -> Path:
    """Create a minimal fake run directory under ``root/runs/agent``.

    ``_plot_run_args`` reads the run dir's INP + OUT via
    ``_find_inp`` / ``_find_out`` glob fallbacks; we don't need a real
    SWMM run, just files in the expected layout.
    """
    run_dir = root / "runs" / "agent" / name
    (run_dir / "04_builder").mkdir(parents=True)
    (run_dir / "05_runner").mkdir(parents=True)
    (run_dir / "04_builder" / "model.inp").write_text("[TITLE]\nfixture\n", encoding="utf-8")
    (run_dir / "05_runner" / "model.out").write_bytes(b"\x00")  # extract() never called from arg mapper
    return run_dir


class PlotRunArgsOverridesDefaultsTests(unittest.TestCase):
    """Cycle 1: the planner-supplied ``rain_ts``/``node``/``node_attr``
    values must propagate into the MCP payload verbatim, so the MCP
    schema defaults (``TS_RAIN`` / ``O1`` / ``Total_inflow``) never
    fire on an agent-driven call."""

    def _build_payload(self, tmp_path: Path) -> dict:
        run_dir = _seed_run_dir(tmp_path)
        call = ToolCall(
            "plot_run",
            {
                "run_dir": str(run_dir.relative_to(tmp_path)),
                "rain_ts": "MACAO_94_23",
                "node": "OUT_0",
                "node_attr": "Total_inflow",
                "rain_kind": "intensity_mm_per_hr",
            },
        )
        return _plot_run_args(call, run_dir)

    def test_explicit_args_propagate_to_mcp_payload(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            # _plot_run_args validates paths are inside repo_root() — point
            # the registry's repo_root at our tmp dir so the fake run_dir
            # under tmp/runs/agent/test-run is accepted.
            from agentic_swmm.agent import tool_registry as registry_mod

            original = registry_mod.repo_root
            registry_mod.repo_root = lambda: tmp  # type: ignore[assignment]
            try:
                payload = self._build_payload(tmp)
            finally:
                registry_mod.repo_root = original  # type: ignore[assignment]

        # Required MCP keys are present.
        self.assertIn("inp", payload)
        self.assertIn("out", payload)
        self.assertIn("outPng", payload)

        # The invariant: planner-supplied values WIN — schema defaults
        # are NEVER what the agent flow puts on the wire.
        self.assertEqual(payload.get("rainTs"), "MACAO_94_23",
                         f"agent flow must propagate explicit rain_ts; got {payload!r}")
        self.assertEqual(payload.get("node"), "OUT_0",
                         f"agent flow must propagate explicit node; got {payload!r}")
        self.assertEqual(payload.get("nodeAttr"), "Total_inflow",
                         f"agent flow must propagate explicit node_attr; got {payload!r}")
        self.assertEqual(payload.get("rainKind"), "intensity_mm_per_hr",
                         f"agent flow must propagate explicit rain_kind; got {payload!r}")

        # Negative assertion: the Tod-Creek-shaped MCP schema defaults
        # MUST NOT appear in the agent payload. These names live in
        # mcp/swmm-plot/server.js only as documentation placeholders
        # for manual CLI use; the agent flow never falls through to them.
        self.assertNotEqual(payload.get("rainTs"), "TS_RAIN")
        self.assertNotEqual(payload.get("rainTs"), "<rainfall-series-name>")
        self.assertNotEqual(payload.get("node"), "O1")
        self.assertNotEqual(payload.get("node"), "<outfall-or-junction>")


class PlotScriptDocumentsDefaultInvariantTests(unittest.TestCase):
    """Cycle 2 regression guard: the documentation that explains the
    'defaults are unreachable in agent path' invariant must remain in
    the script + the MCP server. If a future maintainer deletes the
    docstring or the comment block, this fails — keeping the paper
    reviewer signal alive.
    """

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def test_python_script_docstring_documents_invariant(self) -> None:
        script = self._repo_root() / "skills" / "swmm-plot" / "scripts" / "plot_rain_runoff_si.py"
        text = script.read_text(encoding="utf-8")
        # The module docstring must call out that defaults are CLI-only.
        self.assertIn("manual CLI", text,
                      "plot_rain_runoff_si.py docstring must mention 'manual CLI' "
                      "to flag that the defaults are not used in the agent path.")
        self.assertIn("inspect_plot_options", text,
                      "plot_rain_runoff_si.py must reference inspect_plot_options "
                      "so a paper reviewer sees the agent-side override link.")

    def test_python_script_has_placeholder_defaults(self) -> None:
        script = self._repo_root() / "skills" / "swmm-plot" / "scripts" / "plot_rain_runoff_si.py"
        text = script.read_text(encoding="utf-8")
        self.assertIn("<rainfall-series-name>", text,
                      "plot_rain_runoff_si.py must use the self-documenting "
                      "placeholder default for --rain-ts.")
        self.assertIn("<outfall-or-junction>", text,
                      "plot_rain_runoff_si.py must use the self-documenting "
                      "placeholder default for --node.")

    def test_mcp_server_has_placeholder_defaults(self) -> None:
        server = self._repo_root() / "mcp" / "swmm-plot" / "server.js"
        text = server.read_text(encoding="utf-8")
        self.assertIn("<rainfall-series-name>", text,
                      "mcp/swmm-plot/server.js must use the self-documenting "
                      "placeholder default for rainTs.")
        self.assertIn("<outfall-or-junction>", text,
                      "mcp/swmm-plot/server.js must use the self-documenting "
                      "placeholder default for node.")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
