"""Tests for ``agentic_swmm.agent.swmm_runtime.preflight`` (PRD-06 Phase A.3).

Each check fires against a tiny synthetic INP fixture so the test
clearly demonstrates *what bad input* the check catches. All checks
return PASS / WARN / FAIL with a structured ``detail`` field.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime.preflight import (
    PreflightReport,
    preflight_inp,
)


# Minimal valid INP — every later test mutates this baseline so the
# divergence is the bug under test.
_VALID_INP = """\
[OPTIONS]
FLOW_UNITS           CMS
INFILTRATION         HORTON
FLOW_ROUTING         KINWAVE
START_DATE           01/01/2024
END_DATE             01/02/2024
REPORT_STEP          00:15:00
WET_STEP             00:15:00
DRY_STEP             01:00:00
ROUTING_STEP         60

[RAINGAGES]
RG1              INTENSITY 0:05     1.0      TIMESERIES TS_RAIN

[TIMESERIES]
TS_RAIN          01/01/2024 00:00     0.0

[SUBCATCHMENTS]
S1               RG1              J1             10.0    25.0    100.0   1.0      0

[JUNCTIONS]
J1               100        10        0          0         0

[OUTFALLS]
O1               90         FREE

[CONDUITS]
C1               J1         O1        1000    0.013      0         0          0         0

[REPORT]
"""


def _write_inp(tmp: str, body: str) -> Path:
    path = Path(tmp) / "model.inp"
    path.write_text(body, encoding="utf-8")
    return path


class HappyPathTests(unittest.TestCase):
    def test_valid_inp_returns_pass(self) -> None:
        with TemporaryDirectory() as tmp:
            inp = _write_inp(tmp, _VALID_INP)
            report = preflight_inp(inp)
        self.assertIsInstance(report, PreflightReport)
        self.assertEqual(report.status, "PASS")
        self.assertEqual(report.failures, [])


class ZeroLengthConduitTests(unittest.TestCase):
    def test_conduit_with_zero_length_fails(self) -> None:
        bad = _VALID_INP.replace(
            "C1               J1         O1        1000",
            "C1               J1         O1        0",
        )
        with TemporaryDirectory() as tmp:
            inp = _write_inp(tmp, bad)
            report = preflight_inp(inp)
        self.assertEqual(report.status, "FAIL")
        codes = [f["code"] for f in report.failures]
        self.assertIn("zero_length_conduit", codes)
        # Detail names the offending conduit.
        offender = next(f for f in report.failures if f["code"] == "zero_length_conduit")
        self.assertIn("C1", offender["detail"])


class MissingInvertTests(unittest.TestCase):
    def test_junction_without_elevation_fails(self) -> None:
        # Truncate the JUNCTIONS row so elevation is missing.
        bad = _VALID_INP.replace(
            "J1               100        10        0          0         0",
            "J1",
        )
        with TemporaryDirectory() as tmp:
            inp = _write_inp(tmp, bad)
            report = preflight_inp(inp)
        self.assertEqual(report.status, "FAIL")
        codes = [f["code"] for f in report.failures]
        self.assertIn("missing_invert", codes)

    def test_outfall_without_elevation_fails(self) -> None:
        bad = _VALID_INP.replace(
            "O1               90         FREE",
            "O1",
        )
        with TemporaryDirectory() as tmp:
            inp = _write_inp(tmp, bad)
            report = preflight_inp(inp)
        self.assertEqual(report.status, "FAIL")
        codes = [f["code"] for f in report.failures]
        self.assertIn("missing_invert", codes)


class UndefinedRaingageTests(unittest.TestCase):
    def test_subcatchment_referencing_unknown_raingage_fails(self) -> None:
        # Reference RG_GHOST in the subcatchment row; only RG1 is declared.
        bad = _VALID_INP.replace(
            "S1               RG1              J1",
            "S1               RG_GHOST         J1",
        )
        with TemporaryDirectory() as tmp:
            inp = _write_inp(tmp, bad)
            report = preflight_inp(inp)
        self.assertEqual(report.status, "FAIL")
        codes = [f["code"] for f in report.failures]
        self.assertIn("undefined_raingage", codes)
        offender = next(
            f for f in report.failures if f["code"] == "undefined_raingage"
        )
        self.assertIn("RG_GHOST", offender["detail"])


class FlowUnitsMismatchTests(unittest.TestCase):
    """FLOW_UNITS (CMS) is metric; RAINGAGES INTENSITY in in/hr is US.

    SWMM lets mixed units run silently and the resulting volumes are
    off by 25.4. We surface a WARN — modeler may know they're using
    a translated source — but the agent should not auto-accept the
    run unconditionally.
    """

    def test_cms_flow_with_us_rainfall_warns(self) -> None:
        # Add a comment-marker so we can confirm the WARN body cites units.
        bad = _VALID_INP.replace(
            "RG1              INTENSITY 0:05     1.0      TIMESERIES TS_RAIN",
            "RG1              INTENSITY 0:05     1.0      FILE \"rainfall.dat\" in",
        )
        with TemporaryDirectory() as tmp:
            inp = _write_inp(tmp, bad)
            report = preflight_inp(inp)
        self.assertIn(report.status, {"WARN", "FAIL"})
        codes = [w["code"] for w in report.warnings] + [
            f["code"] for f in report.failures
        ]
        self.assertIn("flow_units_mismatch", codes)


class TimeStepSanityTests(unittest.TestCase):
    """ROUTING_STEP (sub-step) must not exceed WET_STEP (main step).

    SWMM allows it but the numerics get unstable; flagging it before
    the run is faster than reading the .rpt's stability index.
    """

    def test_routing_step_larger_than_wet_step_fails(self) -> None:
        bad = _VALID_INP.replace(
            "WET_STEP             00:15:00",
            "WET_STEP             00:01:00",  # 1 min wet step
        ).replace(
            "ROUTING_STEP         60",
            "ROUTING_STEP         120",  # 2 min routing step (> wet step)
        )
        with TemporaryDirectory() as tmp:
            inp = _write_inp(tmp, bad)
            report = preflight_inp(inp)
        self.assertEqual(report.status, "FAIL")
        codes = [f["code"] for f in report.failures]
        self.assertIn("routing_step_too_large", codes)


if __name__ == "__main__":
    unittest.main()


class CaseSensitivityTests(unittest.TestCase):
    """SWMM accepts lowercase section headers and case-insensitive IDs (review P2-7)."""

    def test_lowercase_section_headers_still_pass(self) -> None:
        import re

        lowered = re.sub(r"\[([A-Z_]+)\]", lambda m: "[" + m.group(1).lower() + "]", _VALID_INP)
        with TemporaryDirectory() as tmp:
            inp = _write_inp(tmp, lowered)
            report = preflight_inp(inp)
        self.assertEqual(report.status, "PASS", report.failures)

    def test_case_mismatched_raingage_not_flagged(self) -> None:
        # Declared RG1 in [RAINGAGES], referenced rg1 in [SUBCATCHMENTS].
        mixed = _VALID_INP.replace("RG1              J1", "rg1              J1")
        with TemporaryDirectory() as tmp:
            inp = _write_inp(tmp, mixed)
            report = preflight_inp(inp)
        codes = [f["code"] for f in report.failures]
        self.assertNotIn("undefined_raingage", codes)
