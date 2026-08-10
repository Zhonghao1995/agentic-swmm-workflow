"""Regression tests: a relative --out-png anchors to the run's plot stage.

Bug (found 2026-08-09 NL sweep, B5): "the run dir is the record", but a
relative ``--out-png`` was resolved against the process CWD, so a
planner-passed bare ``network_map.png`` landed the map in the SESSION
directory instead of the target run's ``08_plot/``. Both ``aiswmm map``
and ``aiswmm plot`` shared the shape.

Fix under test: relative paths resolve under
``<run_dir>/08_plot/``; absolute paths stay verbatim; the no-flag
default is unchanged.
"""

from __future__ import annotations

import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.commands import map as map_cmd


def _make_run(tmp: Path) -> Path:
    run_dir = tmp / "run"
    (run_dir / "05_builder").mkdir(parents=True)
    (run_dir / "05_builder" / "model.inp").write_text(
        "[TITLE]\nx\n", encoding="utf-8"
    )
    return run_dir


def _map_args(run_dir: Path, out_png: Path | None) -> Namespace:
    return Namespace(
        run_dir=run_dir,
        inp=None,
        out_png=out_png,
        dpi=200,
        no_subcatchments=False,
        no_vertices=False,
    )


class MapOutPngAnchorTests(unittest.TestCase):
    def _resolved_out(self, run_dir: Path, out_png: Path | None) -> Path:
        """Run map.main with the renderer stubbed; capture --out-png."""
        captured: dict[str, str] = {}

        def fake_run_command(command, **kwargs):
            idx = command.index("--out-png")
            captured["out"] = command[idx + 1]
            result = mock.Mock(return_code=0, stdout="", stderr="")
            result.as_trace.return_value = {}
            return result

        with mock.patch.object(map_cmd, "run_command", fake_run_command):
            map_cmd.main(_map_args(run_dir, out_png))
        return Path(captured["out"])

    def test_relative_out_png_lands_in_run_plot_stage(self) -> None:
        """Pre-fix: resolved against the process CWD."""
        with TemporaryDirectory() as tmp:
            run_dir = _make_run(Path(tmp))
            out = self._resolved_out(run_dir, Path("network_map.png"))
        self.assertEqual(out.parent.name, "08_plot")
        self.assertEqual(out.parent.parent, run_dir.resolve())

    def test_absolute_out_png_stays_verbatim(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_run(Path(tmp))
            target = Path(tmp) / "elsewhere" / "m.png"
            out = self._resolved_out(run_dir, target)
        self.assertEqual(out, target.resolve())

    def test_default_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_run(Path(tmp))
            out = self._resolved_out(run_dir, None)
        self.assertEqual(out.name, "network_map.png")
        self.assertEqual(out.parent.name, "08_plot")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class PlotMapperAnchorTests(unittest.TestCase):
    """The agent-path plot mapper anchors planner-invented directories
    into the run's canonical plot stage (live 2026-08-09: a freehand
    '08_plots' dir hid the hydrograph from the report scan)."""

    def test_freehand_directory_is_rewritten_to_canonical(self) -> None:
        from unittest import mock

        from agentic_swmm.agent.tool_handlers import swmm_plot
        from agentic_swmm.agent.types import ToolCall
        from agentic_swmm.utils.paths import repo_root

        run_dir = repo_root() / "runs" / "_test_plot_anchor"
        try:
            (run_dir / "06_runner").mkdir(parents=True, exist_ok=True)
            call = ToolCall(
                name="plot_run",
                args={
                    "run_dir": str(run_dir),
                    "node": "N1",
                    "out_png": str(run_dir / "08_plots" / "fig.png"),
                },
            )
            with mock.patch.object(
                swmm_plot, "_resolve_run_dir", return_value=run_dir
            ), mock.patch.object(
                swmm_plot, "_find_inp", return_value=run_dir / "m.inp"
            ), mock.patch.object(
                swmm_plot, "_find_out", return_value=run_dir / "06_runner" / "m.out"
            ):
                (run_dir / "m.inp").write_text("[TITLE]\n", encoding="utf-8")
                (run_dir / "06_runner" / "m.out").write_bytes(b"")
                mapped = swmm_plot._plot_run_args(call, run_dir)
            self.assertIsInstance(mapped, dict)
            self.assertIn("outPng", mapped)
            out = Path(mapped["outPng"])
            self.assertEqual(out.parent.name, "08_plot")
            self.assertEqual(out.name, "fig.png")
        finally:
            import shutil

            shutil.rmtree(run_dir, ignore_errors=True)
