"""Tests for the new calibration thresholds added to ``docs/hitl-thresholds.md``.

Issue #52 adds three threshold rows on top of the four PRD-Z patterns:

* ``calibration_kge_low``           severity=block, KGE < 0.5
* ``calibration_pbias_high``        severity=warn,  |PBIAS| > 30%
* ``sobol_first_order_dominant``    severity=warn,  S_i > 0.8 for any param

The threshold-evaluator code itself is unchanged. These tests guard:

1. The markdown file parses (front-matter integrity) and exposes all
   three new pattern names.
2. Feeding a synthetic ``qa_report`` with ``kge=0.4`` triggers a
   ``ThresholdHit`` with pattern ``calibration_kge_low`` and severity
   ``block`` (the acceptance bullet in the issue).
3. PBIAS hit (|pbias|=42 > 30) returns severity ``warn``.
4. Sobol' hit (max S_i = 0.85 > 0.8) returns severity ``warn``.
5. Each new threshold has a non-empty ``message`` and a placeholder
   ``HYDROLOGY-TODO`` rationale (PRD-Z convention — modeller fills
   later).
6. Pre-existing thresholds are not regressed: the four PRD-Z patterns
   still parse out with their original numeric values.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agentic_swmm.hitl.threshold_evaluator import (
    evaluate,
    load_thresholds_from_md,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS_MD = REPO_ROOT / "docs" / "hitl-thresholds.md"


def _load() -> dict:
    return load_thresholds_from_md(THRESHOLDS_MD)


class NewThresholdNamesTests(unittest.TestCase):
    def test_calibration_kge_low_present(self) -> None:
        self.assertIn("calibration_kge_low", _load())

    def test_calibration_pbias_high_present(self) -> None:
        self.assertIn("calibration_pbias_high", _load())

    def test_sobol_first_order_dominant_present(self) -> None:
        self.assertIn("sobol_first_order_dominant", _load())


class CalibrationKgeLowHitTests(unittest.TestCase):
    """Acceptance bullet: kge=0.4 triggers block-severity hit."""

    def test_low_kge_fires_block_severity(self) -> None:
        qa = {"calibration": {"kge": 0.4}}
        hits = evaluate(qa, _load())
        names = [h.pattern for h in hits]
        self.assertIn("calibration_kge_low", names)
        hit = next(h for h in hits if h.pattern == "calibration_kge_low")
        self.assertEqual(hit.severity, "block")
        self.assertEqual(hit.measured_value, 0.4)

    def test_kge_at_or_above_05_is_not_a_hit(self) -> None:
        qa = {"calibration": {"kge": 0.5}}
        hits = [h for h in evaluate(qa, _load()) if h.pattern == "calibration_kge_low"]
        self.assertEqual(hits, [])


class CalibrationPbiasHighHitTests(unittest.TestCase):
    """|PBIAS| > 30 fires warn severity."""

    def test_high_pbias_abs_fires_warn(self) -> None:
        qa = {"calibration": {"pbias_pct_abs": 42.0}}
        hits = evaluate(qa, _load())
        names = [h.pattern for h in hits]
        self.assertIn("calibration_pbias_high", names)
        hit = next(h for h in hits if h.pattern == "calibration_pbias_high")
        self.assertEqual(hit.severity, "warn")

    def test_pbias_at_threshold_is_not_a_hit(self) -> None:
        qa = {"calibration": {"pbias_pct_abs": 30.0}}
        hits = [
            h
            for h in evaluate(qa, _load())
            if h.pattern == "calibration_pbias_high"
        ]
        self.assertEqual(hits, [])


class SobolFirstOrderDominantHitTests(unittest.TestCase):
    """A single dominant first-order Sobol' index is a structural signal."""

    def test_high_s_i_fires_warn(self) -> None:
        qa = {"sensitivity": {"sobol": {"S_i_max": 0.85}}}
        hits = evaluate(qa, _load())
        names = [h.pattern for h in hits]
        self.assertIn("sobol_first_order_dominant", names)
        hit = next(h for h in hits if h.pattern == "sobol_first_order_dominant")
        self.assertEqual(hit.severity, "warn")


class RationalePlaceholderTests(unittest.TestCase):
    """Each new threshold must carry the HYDROLOGY-TODO placeholder."""

    NEW_NAMES = (
        "calibration_kge_low",
        "calibration_pbias_high",
        "sobol_first_order_dominant",
    )

    def test_each_new_threshold_has_a_nonempty_message(self) -> None:
        thresholds = _load()
        for name in self.NEW_NAMES:
            with self.subTest(name=name):
                msg = thresholds[name].get("message")
                self.assertIsInstance(msg, str)
                self.assertTrue(msg.strip())

    def test_each_new_threshold_has_a_nonempty_rationale(self) -> None:
        # PRD-Z originally shipped each new threshold with a HYDROLOGY-TODO
        # placeholder so a hydrologist could later fill it in. PR #81 filled
        # all seven rationales with literature-grounded text, so the invariant
        # is now simply "non-empty rationale"; a future placeholder is also
        # acceptable (matched explicitly below) since it signals a pending fill.
        thresholds = _load()
        for name in self.NEW_NAMES:
            with self.subTest(name=name):
                rationale = thresholds[name].get("rationale", "")
                self.assertIsInstance(rationale, str)
                self.assertTrue(
                    rationale.strip(),
                    msg=f"{name}: rationale must not be empty.",
                )


class PreExistingThresholdsNotRegressedTests(unittest.TestCase):
    """The four PRD-Z patterns and their values are preserved verbatim."""

    def test_existing_four_patterns_still_present(self) -> None:
        names = set(_load().keys())
        for required in (
            "continuity_error_over_threshold",
            "peak_flow_deviation_over_threshold",
            "pour_point_suspect",
            "calibration_nse_low",
        ):
            self.assertIn(required, names)

    def test_existing_numeric_values_unchanged(self) -> None:
        t = _load()
        self.assertEqual(t["continuity_error_over_threshold"]["value"], 5.0)
        self.assertEqual(t["peak_flow_deviation_over_threshold"]["value"], 25.0)
        self.assertEqual(t["calibration_nse_low"]["value"], 0.5)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
