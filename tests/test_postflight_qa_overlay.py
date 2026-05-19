"""Tests that ``postflight_qa`` honours the project overlay (PRD-07 Phase 4).

The overlay is the per-project hook the maintainer reaches for when a
single threshold needs to be tightened relative to the curated library.
Postflight must (1) consult the overlay first, (2) fall back to the
library, and (3) fall back to a conservative in-module default when
both fail — never disabling the gate.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime.postflight import postflight_qa


_HEALTHY_RPT = """\
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)
  ------------------------------------------------------------

  **************************        Volume         Depth
  Runoff Quantity Continuity     hectare-m            mm
  **************************     ---------       -------
  Total Precipitation ......         0.092        26.208
  Surface Runoff ...........         0.037        10.536
  Continuity Error (%) .....         1.500


  **************************        Volume        Volume
  Flow Routing Continuity        hectare-m      10^6 ltr
  **************************     ---------     ---------
  External Outflow .........         0.037         0.369
  Continuity Error (%) .....         0.200
"""


_LIBRARY_YAML = """
continuity_thresholds_pct:
  runoff:
    warn: 5.0
    fail: 10.0
  flow:
    warn: 1.0
    fail: 5.0
"""


# Overlay tightens runoff WARN to 1.0% — the 1.5% rpt should now WARN
# instead of PASS.
_TIGHT_OVERRIDES_YAML = """
continuity_thresholds_pct:
  runoff:
    warn: 1.0
    fail: 5.0
"""


def _write_rpt(parent: Path, body: str) -> Path:
    run = parent / "run-1"
    run.mkdir()
    (run / "model.rpt").write_text(body, encoding="utf-8")
    return run


class PostflightOverlayTests(unittest.TestCase):
    def test_default_library_runoff_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lib = base / "lib.yaml"
            lib.write_text(_LIBRARY_YAML, encoding="utf-8")
            run = _write_rpt(base, _HEALTHY_RPT)
            report = postflight_qa(run, benchmarks_path=lib)
        self.assertEqual(report.classifications["runoff_continuity_pct"], "PASS")
        self.assertEqual(report.status, "PASS")

    def test_overlay_tightens_runoff_to_warn(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lib = base / "lib.yaml"
            overrides = base / "project_overrides.yaml"
            lib.write_text(_LIBRARY_YAML, encoding="utf-8")
            overrides.write_text(_TIGHT_OVERRIDES_YAML, encoding="utf-8")
            run = _write_rpt(base, _HEALTHY_RPT)
            report = postflight_qa(
                run, benchmarks_path=lib, project_overrides_path=overrides
            )
        self.assertEqual(report.classifications["runoff_continuity_pct"], "WARN")
        self.assertEqual(report.status, "WARN")

    def test_overlay_sibling_of_library_auto_picked_up(self) -> None:
        """When ``project_overrides.yaml`` sits next to the library, it is
        consulted without the caller passing an explicit path."""
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lib = base / "reference_benchmarks.yaml"
            overrides = base / "project_overrides.yaml"
            lib.write_text(_LIBRARY_YAML, encoding="utf-8")
            overrides.write_text(_TIGHT_OVERRIDES_YAML, encoding="utf-8")
            run = _write_rpt(base, _HEALTHY_RPT)
            report = postflight_qa(run, benchmarks_path=lib)
        self.assertEqual(report.classifications["runoff_continuity_pct"], "WARN")

    def test_missing_library_and_missing_overlay_uses_module_fallback(self) -> None:
        """Even when both YAMLs are absent, the runtime gate must still
        classify — the in-module fallback preserves the historical
        SWMM-manual bands."""
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            run = _write_rpt(base, _HEALTHY_RPT)
            absent_lib = base / "no_lib.yaml"
            absent_overlay = base / "no_overlay.yaml"
            report = postflight_qa(
                run,
                benchmarks_path=absent_lib,
                project_overrides_path=absent_overlay,
            )
        # 1.5% runoff < 5% warn → PASS under fallback bands.
        self.assertIn("runoff_continuity_pct", report.classifications)
        self.assertNotEqual(
            report.classifications["runoff_continuity_pct"], "UNKNOWN"
        )

    def test_library_null_leaf_falls_to_module_fallback(self) -> None:
        """A null leaf in the library means "use the conservative
        fallback" — never the literal None which would classify as
        UNKNOWN."""
        null_library = """
continuity_thresholds_pct:
  runoff:
    warn: null
    fail: null
"""
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lib = base / "lib.yaml"
            lib.write_text(null_library, encoding="utf-8")
            run = _write_rpt(base, _HEALTHY_RPT)
            report = postflight_qa(run, benchmarks_path=lib)
        # 1.5% runoff < fallback 5% → PASS, not UNKNOWN.
        self.assertEqual(
            report.classifications["runoff_continuity_pct"], "PASS"
        )


if __name__ == "__main__":
    unittest.main()
