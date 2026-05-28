"""Regression tests for ``--link`` / ``--conduit`` link-level plotting.

Background
----------
The plot path historically only knew how to read node-level series
(``node,<id>,Total_inflow`` etc.) from the SWMM .out. There was no way
to plot a conduit's ``Flow_rate`` time series — a common research need
when inspecting hydraulic capacity / peak flow at the link level.

This adds an alternate selector to both surfaces:

* ``agentic_swmm.commands.plot`` registers ``--link`` (alias
  ``--conduit``); ``--node`` and ``--link`` are mutually exclusive.
* ``skills/swmm-plot/scripts/plot_rain_runoff_si.py`` grows a
  ``--link`` argument; when set, it reads
  ``link,<id>,Flow_rate`` via ``swmmtoolbox.extract`` and uses the
  same paired-axis layout as the node-flow path (rain on top, flow on
  bottom).

The end-to-end "swmm5 run a fixture INP, then plot with --link" test is
gated behind ``swmm5`` availability so CI without the binary still
runs the cheap CLI-protocol tests.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLOT_PY = REPO_ROOT / "skills" / "swmm-plot" / "scripts" / "plot_rain_runoff_si.py"
TODCREEK_INP = REPO_ROOT / "examples" / "todcreek" / "model_chicago5min.inp"


def _has_swmm5() -> bool:
    return shutil.which("swmm5") is not None


def _import_plot_module():
    spec = importlib.util.spec_from_file_location("plot_rain_runoff_si", PLOT_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- CLI-level protocol tests (no SWMM binary needed) ----------------------


class CommandPlotLinkCliProtocolTests(unittest.TestCase):
    """``aiswmm plot`` must register ``--link`` (with ``--conduit`` alias)
    and forward it through to the script. ``--node`` and ``--link`` are
    mutually exclusive."""

    def test_register_adds_link_argument(self) -> None:
        from agentic_swmm.commands import plot

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        plot.register(subparsers)

        # ``--link`` is recognised.
        args = parser.parse_args(["plot", "--run-dir", "x", "--link", "C1"])
        self.assertEqual(getattr(args, "link", None), "C1")

    def test_register_adds_conduit_alias(self) -> None:
        from agentic_swmm.commands import plot

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        plot.register(subparsers)

        args = parser.parse_args(["plot", "--run-dir", "x", "--conduit", "C1"])
        self.assertEqual(getattr(args, "link", None), "C1")

    def test_node_and_link_are_mutually_exclusive(self) -> None:
        from agentic_swmm.commands import plot

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        plot.register(subparsers)

        # Mixing --node and --link should fail at argparse time.
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["plot", "--run-dir", "x", "--node", "J1", "--link", "C1"]
            )

    def test_main_forwards_link_when_set(self) -> None:
        """``commands/plot.py::main`` source must forward ``--link``
        through to the subprocess command when ``args.link`` is set."""
        from agentic_swmm.commands import plot

        src = inspect.getsource(plot.main)
        self.assertIn(
            "--link",
            src,
            "commands/plot.py:main must forward --link to plot_rain_runoff_si.py",
        )


# --- Script-level argparse test (no SWMM binary needed) --------------------


class PlotScriptLinkArgumentTests(unittest.TestCase):
    """``plot_rain_runoff_si.py`` must accept ``--link`` as an alternate
    selector to ``--node``."""

    def test_script_source_advertises_link(self) -> None:
        text = PLOT_PY.read_text(encoding="utf-8")
        self.assertIn(
            "--link",
            text,
            "plot_rain_runoff_si.py must register a --link argument so "
            "the CLI-side --link can reach the renderer.",
        )

    def test_script_reads_link_flow_rate_attribute(self) -> None:
        """The renderer must extract ``link,<id>,Flow_rate`` (not
        ``node,...,Total_inflow``) when ``--link`` is provided."""
        text = PLOT_PY.read_text(encoding="utf-8")
        self.assertIn(
            "Flow_rate",
            text,
            "plot_rain_runoff_si.py must reference link Flow_rate "
            "attribute when --link is used.",
        )
        self.assertIn(
            "link,",
            text,
            "plot_rain_runoff_si.py must build a 'link,<id>,Flow_rate' "
            "swmmtoolbox key when --link is used.",
        )


# --- End-to-end smoke test (only if swmm5 available) -----------------------


@pytest.mark.skipif(not _has_swmm5(), reason="swmm5 binary not available on PATH")
class PlotScriptLinkEndToEndTests(unittest.TestCase):
    """End-to-end: run swmm5 on the Todcreek fixture, then render a
    link-level Flow_rate plot. Asserts the PNG is produced and contains
    actual rendered content (>5 KB)."""

    @classmethod
    def setUpClass(cls) -> None:
        if not TODCREEK_INP.exists():
            raise unittest.SkipTest("Todcreek fixture missing")

        cls._tmp_root = Path(tempfile.mkdtemp(prefix="plot-link-"))
        cls.inp = cls._tmp_root / "model.inp"
        cls.inp.write_text(TODCREEK_INP.read_text(), encoding="utf-8")
        rpt = cls._tmp_root / "model.rpt"
        cls.out_file = cls._tmp_root / "model.out"
        proc = subprocess.run(
            ["swmm5", str(cls.inp), str(rpt), str(cls.out_file)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"swmm5 failed building fixture: {proc.stderr}")

        # Discover a link id from the [CONDUITS] block.
        cls.link_id = cls._find_first_conduit_id(cls.inp)
        if cls.link_id is None:
            raise unittest.SkipTest("Todcreek INP has no [CONDUITS] entries")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp_root, ignore_errors=True)

    @staticmethod
    def _find_first_conduit_id(inp: Path) -> str | None:
        in_conduits = False
        for raw in inp.read_text().splitlines():
            s = raw.strip()
            if s.upper() == "[CONDUITS]":
                in_conduits = True
                continue
            if in_conduits:
                if s.startswith("[") and s.endswith("]"):
                    break
                if not s or s.startswith(";"):
                    continue
                return s.split()[0]
        return None

    def setUp(self) -> None:
        import matplotlib.pyplot as plt  # noqa: WPS433

        plt.close("all")
        self.png = Path(tempfile.mkdtemp(prefix="plot-link-out-")) / "fig.png"
        self.addCleanup(lambda: shutil.rmtree(self.png.parent, ignore_errors=True))

    def test_link_flow_rate_renders_png(self) -> None:
        mod = _import_plot_module()
        argv = [
            "plot_rain_runoff_si",
            "--inp", str(self.inp),
            "--out", str(self.out_file),
            "--rain-ts", "TS_RAIN",
            "--rain-kind", "intensity_mm_per_hr",
            "--link", self.link_id,
            "--out-png", str(self.png),
            "--dpi", "80",
        ]
        old_argv = sys.argv[:]
        sys.argv = argv
        try:
            mod.main()
        finally:
            sys.argv = old_argv

        self.assertTrue(self.png.exists(), f"PNG was not written to {self.png}")
        self.assertGreater(
            self.png.stat().st_size,
            5_000,
            "PNG is suspiciously small — link flow rendering may be blank.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
