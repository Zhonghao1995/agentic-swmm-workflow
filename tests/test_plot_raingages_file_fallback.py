"""Regression test for plot script reading rainfall from ``[RAINGAGES] FILE``.

Motivation
----------
SWMManywhere-generated INPs use the ``[RAINGAGES]`` section with a
``FILE`` reference instead of an inline ``[TIMESERIES]`` block, e.g.::

    [RAINGAGES]
    ;;Name           Format     Interval SCF      Source
    rg1              INTENSITY  0:05     1.0      FILE     "storm.dat"

The companion ``storm.dat`` file uses the SWMM5 RAINGAGES FILE format::

    <gage_id> <YYYY> <MM> <DD> <HH> <mm> <intensity_mm_per_hr>

Before the fallback was added, ``commands/plot.py::_infer_rain_timeseries``
raised ``Unable to infer rainfall TIMESERIES from INP`` because
``rainfall_timeseries_options`` only scanned ``[TIMESERIES]``. The plot
script's ``parse_timeseries_from_inp`` would also fail with
``No TIMESERIES values found`` when handed such a name.

This test pins the new behaviour: both surfaces correctly resolve the
RAINGAGES-FILE-only rainfall input and return a usable (times, values)
series for downstream plotting.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLOT_PY = REPO_ROOT / "skills" / "swmm-plot" / "scripts" / "plot_rain_runoff_si.py"


_INP_RAINGAGES_FILE = """[TITLE]
SWMManywhere-shaped synthetic fixture

[OPTIONS]
FLOW_UNITS           CMS
START_DATE           01/01/2024
END_DATE             01/01/2024

[RAINGAGES]
;;Name           Format     Interval SCF      Source
rg1              INTENSITY  0:05     1.0      FILE     "storm.dat"

[SUBCATCHMENTS]
"""


# Two consecutive 5-min steps so we exercise the parser loop, not just
# a single line. Format is SWMM5 RAINGAGES FILE intensity: mm/h.
_STORM_DAT_CONTENT = """rg1 2024 01 01 00 00 12.0
rg1 2024 01 01 00 05 24.0
rg1 2024 01 01 00 10 0.0
"""


def _import_plot_module():
    """Load ``plot_rain_runoff_si`` as a module so we can exercise the
    parser helpers directly without re-running the full ``main()``."""
    spec = importlib.util.spec_from_file_location("plot_rain_runoff_si", PLOT_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RainfallTimeseriesOptionsRaingagesFileTests(unittest.TestCase):
    """``commands/plot.py::rainfall_timeseries_options`` must surface the
    RAINGAGES-FILE entry so ``_infer_rain_timeseries`` doesn't raise."""

    def test_options_include_raingages_file_entry(self) -> None:
        from agentic_swmm.agent.swmm_runtime.inp_parsing import rainfall_timeseries_options

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            inp = tmp / "model.inp"
            inp.write_text(_INP_RAINGAGES_FILE, encoding="utf-8")
            (tmp / "storm.dat").write_text(_STORM_DAT_CONTENT, encoding="utf-8")

            options = rainfall_timeseries_options(inp)

        self.assertGreaterEqual(
            len(options), 1,
            f"RAINGAGES FILE input should surface at least one option; got {options!r}",
        )
        # The synthetic option should be marked used_by_raingage so
        # ``_infer_rain_timeseries`` returns it on the first pass.
        chosen = options[0]
        self.assertTrue(
            chosen.get("used_by_raingage"),
            f"RAINGAGES FILE entry must be marked used_by_raingage; got {chosen!r}",
        )
        # Name should be derivable (the gage name itself is the natural
        # choice when no [TIMESERIES] block exists).
        self.assertEqual(chosen.get("name"), "rg1")

    def test_infer_rain_timeseries_does_not_raise(self) -> None:
        from agentic_swmm.agent.swmm_runtime.inp_parsing import (
            infer_rain_timeseries as _infer_rain_timeseries,
        )

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            inp = tmp / "model.inp"
            inp.write_text(_INP_RAINGAGES_FILE, encoding="utf-8")
            (tmp / "storm.dat").write_text(_STORM_DAT_CONTENT, encoding="utf-8")

            # Previously raised FileNotFoundError. Now returns a name.
            name, _kind = _infer_rain_timeseries(inp)

        self.assertEqual(name, "rg1")


class ScriptParseTimeseriesFromInpRaingagesFileTests(unittest.TestCase):
    """``plot_rain_runoff_si.py::parse_timeseries_from_inp`` must read the
    referenced ``.dat`` file when no ``[TIMESERIES]`` block exists."""

    def test_parse_falls_back_to_raingages_file(self) -> None:
        mod = _import_plot_module()
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            inp = tmp / "model.inp"
            inp.write_text(_INP_RAINGAGES_FILE, encoding="utf-8")
            (tmp / "storm.dat").write_text(_STORM_DAT_CONTENT, encoding="utf-8")

            times, vals = mod.parse_timeseries_from_inp(inp, "rg1")

        # 3 storm.dat rows = 3 samples
        self.assertEqual(len(times), 3)
        self.assertEqual(len(vals), 3)
        self.assertEqual(times[0], datetime(2024, 1, 1, 0, 0))
        self.assertEqual(times[1], datetime(2024, 1, 1, 0, 5))
        self.assertEqual(times[2], datetime(2024, 1, 1, 0, 10))
        self.assertAlmostEqual(vals[0], 12.0)
        self.assertAlmostEqual(vals[1], 24.0)
        self.assertAlmostEqual(vals[2], 0.0)

    def test_parse_legacy_timeseries_still_works(self) -> None:
        """Strict additive: the existing ``[TIMESERIES]`` path must keep
        working when both formats are present (or the legacy-only case)."""
        mod = _import_plot_module()
        legacy_inp_text = """[TITLE]
legacy fixture

[TIMESERIES]
;;Name           Date       Time    Value
TS_RAIN          01/01/2024 00:00   12.0
TS_RAIN          01/01/2024 00:05   24.0

[SUBCATCHMENTS]
"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            inp = tmp / "model.inp"
            inp.write_text(legacy_inp_text, encoding="utf-8")
            times, vals = mod.parse_timeseries_from_inp(inp, "TS_RAIN")

        self.assertEqual(len(times), 2)
        self.assertAlmostEqual(vals[0], 12.0)
        self.assertAlmostEqual(vals[1], 24.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
