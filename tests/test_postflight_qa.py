"""Tests for ``agentic_swmm.agent.swmm_runtime.postflight`` (PRD-06 Phase A.4).

Postflight reads the .rpt produced by SWMM, extracts continuity (runoff,
flow), classifies each metric against ``reference_benchmarks.yaml``
thresholds, and returns a structured ``QAReport``.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime.postflight import (
    QAReport,
    parse_continuity_from_rpt,
    postflight_qa,
)


_HEALTHY_RPT = """\
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)
  ------------------------------------------------------------

  Saanich framework smoke test from raw GIS assets

  ****************
  Analysis Options
  ****************
  Flow Units ............... CMS
  Flow Routing Method ...... KINWAVE

  **************************        Volume         Depth
  Runoff Quantity Continuity     hectare-m            mm
  **************************     ---------       -------
  Total Precipitation ......         0.092        26.208
  Surface Runoff ...........         0.037        10.536
  Continuity Error (%) .....        -0.171


  **************************        Volume        Volume
  Flow Routing Continuity        hectare-m      10^6 ltr
  **************************     ---------     ---------
  External Outflow .........         0.037         0.369
  Continuity Error (%) .....         0.000
"""


_BAD_CONTINUITY_RPT = _HEALTHY_RPT.replace(
    "Continuity Error (%) .....        -0.171",
    "Continuity Error (%) .....        12.500",  # FAIL: > 10%
).replace(
    "Continuity Error (%) .....         0.000",
    "Continuity Error (%) .....         3.200",  # WARN: > 1% < 5%
)


def _write_run_dir(tmp: str, rpt_body: str) -> Path:
    run_dir = Path(tmp) / "run-abc"
    run_dir.mkdir()
    (run_dir / "model.rpt").write_text(rpt_body, encoding="utf-8")
    return run_dir


class ContinuityParserTests(unittest.TestCase):
    def test_parses_runoff_and_flow_continuity_signed(self) -> None:
        parsed = parse_continuity_from_rpt(_HEALTHY_RPT)
        self.assertAlmostEqual(parsed["runoff_continuity_pct"], -0.171, places=3)
        self.assertAlmostEqual(parsed["flow_continuity_pct"], 0.000, places=3)

    def test_parses_when_only_runoff_present(self) -> None:
        # Slice the .rpt at the point just before the Flow Routing block.
        flow_idx = next(
            i
            for i, line in enumerate(_HEALTHY_RPT.splitlines())
            if "Flow Routing Continuity" in line
        )
        only_runoff = "\n".join(_HEALTHY_RPT.splitlines()[: flow_idx - 1])
        parsed = parse_continuity_from_rpt(only_runoff)
        self.assertIn("runoff_continuity_pct", parsed)
        self.assertNotIn("flow_continuity_pct", parsed)

    def test_returns_empty_dict_on_text_without_continuity(self) -> None:
        parsed = parse_continuity_from_rpt("ERROR 1: something blew up.")
        self.assertEqual(parsed, {})


class PostflightAggregationTests(unittest.TestCase):
    def test_healthy_run_is_pass(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _write_run_dir(tmp, _HEALTHY_RPT)
            report = postflight_qa(run_dir)
        self.assertIsInstance(report, QAReport)
        self.assertEqual(report.status, "PASS")
        self.assertEqual(report.failures, [])
        # Metrics still surface so the audit note can render them.
        self.assertIn("runoff_continuity_pct", report.metrics)
        self.assertEqual(
            report.classifications["runoff_continuity_pct"], "PASS"
        )

    def test_bad_continuity_marks_fail_and_warn(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _write_run_dir(tmp, _BAD_CONTINUITY_RPT)
            report = postflight_qa(run_dir)
        self.assertEqual(report.status, "FAIL")
        self.assertEqual(
            report.classifications["runoff_continuity_pct"], "FAIL"
        )
        self.assertEqual(
            report.classifications["flow_continuity_pct"], "WARN"
        )
        # Failure list cites the metric name.
        codes = [f["code"] for f in report.failures]
        self.assertIn("runoff_continuity_pct", codes)

    def test_missing_rpt_is_fail(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "empty"
            run_dir.mkdir()
            report = postflight_qa(run_dir)
        self.assertEqual(report.status, "FAIL")
        codes = [f["code"] for f in report.failures]
        self.assertIn("rpt_missing", codes)

    def test_custom_thresholds_path_overrides_default(self) -> None:
        # Use a thresholds file that's deliberately strict.
        strict_yaml = (
            "continuity_thresholds_pct:\n"
            "  runoff:\n"
            "    warn: 0.1\n"
            "    fail: 0.5\n"
            "  flow:\n"
            "    warn: 0.1\n"
            "    fail: 0.5\n"
        )
        with TemporaryDirectory() as tmp:
            run_dir = _write_run_dir(tmp, _HEALTHY_RPT)
            thresholds = Path(tmp) / "strict.yaml"
            thresholds.write_text(strict_yaml, encoding="utf-8")
            report = postflight_qa(run_dir, benchmarks_path=thresholds)
        # |-0.171| > 0.1 warn but < 0.5 fail -> WARN
        self.assertEqual(
            report.classifications["runoff_continuity_pct"], "WARN"
        )
        # 0.000 -> PASS
        self.assertEqual(
            report.classifications["flow_continuity_pct"], "PASS"
        )
        self.assertEqual(report.status, "WARN")


if __name__ == "__main__":
    unittest.main()
