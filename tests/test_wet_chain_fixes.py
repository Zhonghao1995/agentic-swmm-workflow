"""Regression tests: two live findings from the wet-window Canada chain.

B7: ``plot_rain_runoff_si.py`` parsed inline ``[TIMESERIES]`` (and
external timeseries-file) datetimes with a single hardcoded
``%m/%d/%Y %H:%M`` format. The SWMMCanada upstream emits
second-precision stamps (``HH:MM:SS``), so EVERY fetched Canadian
model crashed the plot skill with "unconverted data remains: :00"
(observed live 2026-08-09 on a 106 mm November wet-window model).

B9: ``review_run`` treated the design-review FAIL VERDICT (script exit
code 1) as an execution failure, so every honest FAIL burned one of
the planner failure checkpoint's strikes. Verdict is data: exit 1 with
a verdict line now reports ok with ``verdict: fail``; exit codes >= 2
remain genuine execution failures.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent import tool_handlers
from agentic_swmm.agent.tool_handlers import swmm_review
from agentic_swmm.agent.types import ToolCall


_PLOT_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills/swmm-plot/scripts/plot_rain_runoff_si.py"
)


def _load_plot_module():
    spec = importlib.util.spec_from_file_location("_plot_ts_test", _PLOT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TimeseriesSecondsToleranceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_plot_module()

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("_plot_ts_test", None)

    def test_both_time_shapes_parse(self) -> None:
        f = self.mod._parse_inline_ts_datetime
        self.assertEqual(f("11/01/2023", "05:00"), datetime(2023, 11, 1, 5, 0))
        self.assertEqual(
            f("11/01/2023", "05:00:00"), datetime(2023, 11, 1, 5, 0)
        )

    def test_unparseable_fails_loudly_with_formats_listed(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.mod._parse_inline_ts_datetime("2023-11-01", "05h00")
        self.assertIn("%m/%d/%Y %H:%M", str(ctx.exception))

    def test_inline_timeseries_with_seconds_parses_end_to_end(self) -> None:
        """The live crash shape: SWMMCanada inline rows with seconds."""
        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text(
                "[TIMESERIES]\n"
                ";;Name  Date       Time      Value\n"
                "rain    11/01/2023 05:00:00  1.20\n"
                "rain    11/01/2023 06:00:00  2.40\n"
                "\n[REPORT]\n",
                encoding="utf-8",
            )
            times, vals = self.mod.parse_timeseries_from_inp(inp, "rain")
        self.assertEqual(len(times), 2)
        self.assertEqual(times[0], datetime(2023, 11, 1, 5, 0))
        self.assertEqual(vals, [1.2, 2.4])

    def test_external_timeseries_file_with_seconds_parses(self) -> None:
        with TemporaryDirectory() as tmp:
            dat = Path(tmp) / "storm.txt"
            dat.write_text(
                "11/01/2023 05:00:00 1.5\n11/01/2023 06:00:00 0.5\n",
                encoding="utf-8",
            )
            times, vals = self.mod.parse_timeseries_file(dat)
        self.assertEqual(times[0], datetime(2023, 11, 1, 5, 0))
        self.assertEqual(vals, [1.5, 0.5])


class ReviewVerdictIsDataTests(unittest.TestCase):
    def _call(self, rc: int, stdout_tail: str) -> dict:
        base = {
            "tool": "review_run",
            "args": {},
            "ok": rc == 0,
            "return_code": rc,
            "stdout_tail": stdout_tail,
            "summary": "review_run failed" if rc else "ok",
        }
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            with mock.patch.object(
                swmm_review, "_resolve_run_dir", return_value=run_dir
            ), mock.patch.object(
                swmm_review, "_run_script_tool", return_value=dict(base)
            ):
                call = ToolCall(name="review_run", args={"run_dir": str(run_dir)})
                return swmm_review._review_run_tool(call, Path(tmp))

    def test_fail_verdict_reports_ok_with_verdict(self) -> None:
        """Pre-fix: burned a failure-checkpoint strike."""
        result = self._call(
            1, "Design review: FAIL (1 pass, 2 fail, 4 warn, 4 needs-data)\n  Report: x.md"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "fail")
        self.assertIn("Design review: FAIL", result["summary"])

    def test_exit_two_stays_an_execution_failure(self) -> None:
        result = self._call(2, "ERROR: model.rpt not found")
        self.assertFalse(result["ok"])
        self.assertNotIn("verdict", result)

    def test_exit_one_without_verdict_line_stays_failed(self) -> None:
        result = self._call(1, "Traceback (most recent call last): ...")
        self.assertFalse(result["ok"])


class CanadaReportSectionInjectionTests(unittest.TestCase):
    """B10: SWMMCanada INPs omit [REPORT], so the binary .out carried no
    per-element series and hydrograph plotting was impossible. The
    landing step now injects a full [REPORT] section; models that
    already carry one stay byte-for-byte untouched."""

    def test_missing_report_section_is_injected(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import (
            _ensure_report_section,
        )

        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\ncanada\n[OPTIONS]\nFLOW_UNITS LPS\n", encoding="utf-8")
            self.assertTrue(_ensure_report_section(inp))
            text = inp.read_text(encoding="utf-8")
        self.assertIn("[REPORT]", text)
        self.assertIn("NODES ALL", text)
        self.assertIn("LINKS ALL", text)
        self.assertIn("SUBCATCHMENTS ALL", text)

    def test_existing_report_section_untouched(self) -> None:
        from agentic_swmm.integrations.swmmcanada_runner import (
            _ensure_report_section,
        )

        original = "[TITLE]\nx\n[REPORT]\nNODES O1\n"
        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text(original, encoding="utf-8")
            self.assertFalse(_ensure_report_section(inp))
            self.assertEqual(inp.read_text(encoding="utf-8"), original)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
