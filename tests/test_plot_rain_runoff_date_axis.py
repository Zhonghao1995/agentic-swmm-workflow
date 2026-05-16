"""Regression test for issue #112: plot X-axis renders as a black blur.

Background
----------
``skills/swmm-plot/scripts/plot_rain_runoff_si.py`` used to fall through
to matplotlib's default ``AutoLocator`` whenever the caller did not pass
``--focus-day`` *and* the rainfall series had no nonzero samples in
range. For a year-long SWMM simulation (Tecnopolo 1994), this produced
plots with hundreds of tick labels overlapping into a solid black bar
on the X-axis — unreadable in print. The fix is to install an
``AutoDateLocator`` + ``ConciseDateFormatter`` so the tick density is
bounded regardless of the simulated duration.

Fixture choice
--------------
The committed ``examples/todcreek/model_chicago5min.inp`` is a 5-day
demo where rainfall is concentrated in a single ~10 h storm. The
auto-window logic in ``plot_rain_runoff_si.py`` would zoom into that
storm and dodge the bug entirely. So we generate a multi-day variant
on the fly: same Todcreek geometry, ``START_DATE``/``END_DATE``
stretched to 30 days, with rainfall events at the beginning *and* end
so the auto-window has to span the full month. ``swmm5`` runs against
that patched INP to materialise a real ``model.out`` (~2-3 s; cached
once per test class). The synthetic alternative — fabricating a SWMM
binary ourselves — would couple the test to swmmtoolbox's on-disk
layout, exactly the implementation detail the regression must NOT
care about. Tests skip cleanly if ``swmm5`` is missing on the runner.

A 30-day window with the legacy ``HourLocator(interval=2)`` would
emit 30*24/2 = 360 tick labels — well into black-blur territory.
The Cycle 2 sub-day case uses the same fixture with ``--focus-day``
to exercise the existing ``HourLocator(interval=3)`` short-duration
branch and pin that ``HH:MM`` labels survive the refactor.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INP_FIXTURE = REPO_ROOT / "examples" / "todcreek" / "model_chicago5min.inp"
PLOT_PY = REPO_ROOT / "skills" / "swmm-plot" / "scripts" / "plot_rain_runoff_si.py"


def _has_swmm5() -> bool:
    return shutil.which("swmm5") is not None


def _stretch_inp_to_30_days(inp_text: str) -> str:
    """Return the Todcreek INP retargeted to a 30-day simulation with
    rainfall at both endpoints. This forces ``plot_rain_runoff_si.py``'s
    auto-window logic to span the whole month, which is the X-axis
    duration that triggers the #112 black-blur."""
    text = inp_text
    text = text.replace("END_DATE             05/28/1984", "END_DATE             06/22/1984")
    # The TIMESERIES section already opens with TS_RAIN 05/23 00:00 0
    # and ends with TS_RAIN 05/27 23:55 0. Add a trailing zero anchor
    # near the new END_DATE so auto-window stretches to ~30 days.
    extra_anchor = (
        "TS_RAIN          06/20/1984 12:00     0\n"
        "TS_RAIN          06/20/1984 12:05     1.5\n"
        "TS_RAIN          06/20/1984 12:10     0\n"
    )
    text = text.replace(
        "TS_RAIN          05/27/1984 23:55     0\n",
        "TS_RAIN          05/27/1984 23:55     0\n" + extra_anchor,
    )
    return text


def _import_plot_module():
    """Load ``plot_rain_runoff_si`` as a module so we can call ``main()``
    in-process and capture the matplotlib ``Figure`` afterwards.
    """
    spec = importlib.util.spec_from_file_location("plot_rain_runoff_si", PLOT_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not _has_swmm5(), reason="swmm5 binary not available on PATH")
class PlotXAxisTickDensityRegression(unittest.TestCase):
    """Pin the #112 fix: tick density on the X-axis is bounded."""

    @classmethod
    def setUpClass(cls) -> None:
        if not PLOT_PY.exists():  # pragma: no cover - sanity guard
            raise unittest.SkipTest("plot_rain_runoff_si.py is missing")
        if not INP_FIXTURE.exists():  # pragma: no cover
            raise unittest.SkipTest("Todcreek fixture INP missing")

        cls._tmp_root = Path(tempfile.mkdtemp(prefix="plot-xaxis-"))
        cls.inp = cls._tmp_root / "model.inp"
        cls.inp.write_text(
            _stretch_inp_to_30_days(INP_FIXTURE.read_text()),
            encoding="utf-8",
        )
        rpt = cls._tmp_root / "model.rpt"
        cls.out_file = cls._tmp_root / "model.out"
        proc = subprocess.run(
            ["swmm5", str(cls.inp), str(rpt), str(cls.out_file)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:  # pragma: no cover - fixture must run
            raise RuntimeError(f"swmm5 failed building fixture: {proc.stderr}")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp_root, ignore_errors=True)

    def setUp(self) -> None:
        # Fresh matplotlib state per test so figure introspection is
        # deterministic.
        import matplotlib.pyplot as plt  # noqa: WPS433

        plt.close("all")
        self.png = Path(tempfile.mkdtemp(prefix="plot-xaxis-out-")) / "fig.png"
        self.addCleanup(lambda: shutil.rmtree(self.png.parent, ignore_errors=True))

    def _invoke_plot_main(self, *extra_argv: str) -> object:
        """Run ``plot_rain_runoff_si.main()`` in-process and return the
        produced ``matplotlib.figure.Figure`` instance.
        """
        import matplotlib.pyplot as plt  # noqa: WPS433

        mod = _import_plot_module()
        argv = [
            "plot_rain_runoff_si",
            "--inp", str(self.inp),
            "--out", str(self.out_file),
            "--rain-ts", "TS_RAIN",
            "--rain-kind", "intensity_mm_per_hr",
            "--node", "O1",
            "--node-attr", "Total_inflow",
            "--out-png", str(self.png),
            "--dpi", "80",
            *extra_argv,
        ]
        old_argv = sys.argv[:]
        sys.argv = argv
        try:
            mod.main()
        finally:
            sys.argv = old_argv
        figs = [plt.figure(i) for i in plt.get_fignums()]
        assert figs, "plot_rain_runoff_si.main() did not produce a figure"
        return figs[-1]

    # --- Cycle 1: long-duration tick density ---------------------------------

    def test_long_run_xaxis_has_bounded_tick_count(self) -> None:
        """The X-axis must have at most ~15 visible labels for a multi-day
        run. Before the fix, this was hundreds (the #112 black-blur)."""
        fig = self._invoke_plot_main()
        ax_rain = fig.axes[0]
        labels = [t for t in ax_rain.get_xticklabels() if t.get_text()]
        self.assertGreaterEqual(
            len(labels), 2,
            f"Too few tick labels ({len(labels)}); axis is effectively blank.",
        )
        self.assertLessEqual(
            len(labels), 15,
            f"Too many tick labels ({len(labels)}); the #112 black-blur "
            f"is back. Labels: {[t.get_text() for t in labels]!r}",
        )

    def test_long_run_writes_nontrivial_png(self) -> None:
        """The script must actually save the PNG to disk and the file
        must be large enough to indicate real plot content rendered
        (i.e., not a blank canvas)."""
        self._invoke_plot_main()
        self.assertTrue(
            self.png.exists(),
            f"plot_rain_runoff_si.main() did not save {self.png}.",
        )
        size = self.png.stat().st_size
        self.assertGreater(
            size, 5_000,
            f"PNG at {self.png} is {size} bytes — suspiciously small; "
            f"expected >5 KB of rendered plot content.",
        )

    # --- Cycle 2: sub-day focus retains HH:MM style --------------------------

    def test_focus_day_xaxis_reads_as_hours_minutes(self) -> None:
        """When ``--focus-day`` narrows the X-axis to a single day, the
        labels must read as ``HH:MM`` (zero-padded hour and minute), not
        as dates. This pins the existing sub-day formatting against any
        future consolidation of the locator/formatter setup."""
        import re

        fig = self._invoke_plot_main("--focus-day", "1984-05-25")
        ax_rain = fig.axes[0]
        labels = [t.get_text() for t in ax_rain.get_xticklabels() if t.get_text()]
        self.assertTrue(labels, "Expected some HH:MM tick labels, got none.")
        hhmm = re.compile(r"^\d{2}:\d{2}$")
        bad = [s for s in labels if not hhmm.match(s)]
        self.assertFalse(
            bad,
            f"Sub-day focus must label ticks as HH:MM, got: {labels!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
