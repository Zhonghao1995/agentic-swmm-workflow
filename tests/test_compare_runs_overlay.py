"""Tests that ``compare_runs`` honours the project overlay (PRD-07 Phase 4).

The overlay reaches two surfaces of ``compare_runs``:

1. Per-metric classification — runs that PASSed under the library may
   WARN under a tighter overlay, and the verdict tracks the
   reclassification.
2. The tiebreaker continuity-magnitude tolerance, which is now
   resolvable through the same overlay key
   ``compare_runs.tie_tol_continuity_magnitude``.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime.compare import compare_runs


_LIBRARY_YAML = """
continuity_thresholds_pct:
  runoff:
    warn: 5.0
    fail: 10.0
  flow:
    warn: 1.0
    fail: 5.0
"""


# Both runs pass under the library but only one passes under the tighter
# overlay — verifies the overlay reaches per-metric classification in
# the comparison.
_TIGHT_OVERRIDES_YAML = """
continuity_thresholds_pct:
  runoff:
    warn: 1.0
    fail: 5.0
"""


_LOW_CONTINUITY_RPT = """\
  Runoff Quantity Continuity
  Continuity Error (%) .....         0.500
  Flow Routing Continuity
  Continuity Error (%) .....         0.200
"""


_MID_CONTINUITY_RPT = """\
  Runoff Quantity Continuity
  Continuity Error (%) .....         2.500
  Flow Routing Continuity
  Continuity Error (%) .....         0.200
"""


def _make_run(parent: Path, name: str, body: str) -> Path:
    d = parent / name
    d.mkdir()
    (d / "model.rpt").write_text(body, encoding="utf-8")
    return d


class CompareOverlayClassificationTests(unittest.TestCase):
    def test_overlay_changes_metric_classification(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lib = base / "lib.yaml"
            overrides = base / "project_overrides.yaml"
            lib.write_text(_LIBRARY_YAML, encoding="utf-8")
            overrides.write_text(_TIGHT_OVERRIDES_YAML, encoding="utf-8")
            run_a = _make_run(base, "a", _LOW_CONTINUITY_RPT)
            run_b = _make_run(base, "b", _MID_CONTINUITY_RPT)
            result = compare_runs(
                run_a,
                run_b,
                benchmarks_path=lib,
                project_overrides_path=overrides,
            )
        diff = result.metric_diffs["runoff_continuity_pct"]
        self.assertEqual(diff.classification_a, "PASS")
        # Under overlay (warn=1.0), 2.5% runoff is WARN.
        self.assertEqual(diff.classification_b, "WARN")
        self.assertEqual(result.verdict, "a_better")

    def test_no_overlay_keeps_library_classification(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lib = base / "lib.yaml"
            lib.write_text(_LIBRARY_YAML, encoding="utf-8")
            run_a = _make_run(base, "a", _LOW_CONTINUITY_RPT)
            run_b = _make_run(base, "b", _MID_CONTINUITY_RPT)
            result = compare_runs(run_a, run_b, benchmarks_path=lib)
        diff = result.metric_diffs["runoff_continuity_pct"]
        # Both <5% library warn → PASS.
        self.assertEqual(diff.classification_a, "PASS")
        self.assertEqual(diff.classification_b, "PASS")


_LIBRARY_WITH_TIE_TOL_YAML = """
continuity_thresholds_pct:
  runoff:
    warn: 5.0
    fail: 10.0
compare_runs:
  tie_tol_continuity_magnitude: 1.0
"""


class CompareOverlayTieTolTests(unittest.TestCase):
    """The overlay can loosen the tiebreaker tolerance so two runs that
    look slightly different under the strict 1e-3 floor read as a tie."""

    def test_library_tie_tol_pushes_close_runs_to_tie(self) -> None:
        # Both runs PASS under the library; their magnitudes differ by
        # only ~0.3 (0.5 vs 0.7 runoff), within the 1.0 tolerance.
        body_a = """\
  Runoff Quantity Continuity
  Continuity Error (%) .....         0.500
"""
        body_b = """\
  Runoff Quantity Continuity
  Continuity Error (%) .....         0.700
"""
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lib = base / "lib.yaml"
            lib.write_text(_LIBRARY_WITH_TIE_TOL_YAML, encoding="utf-8")
            run_a = _make_run(base, "a", body_a)
            run_b = _make_run(base, "b", body_b)
            result = compare_runs(run_a, run_b, benchmarks_path=lib)
        # Under default tie_tol (1e-3) this would be a_better; with the
        # library-resolved 1.0 it's a tie.
        self.assertEqual(result.verdict, "tie")


if __name__ == "__main__":
    unittest.main()
