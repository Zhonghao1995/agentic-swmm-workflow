"""Tests for ``agentic_swmm.hitl.threshold_evaluator`` (PRD-Z).

The evaluator is a pure function: ``evaluate(qa_report, thresholds)``
returns a list of ``ThresholdHit`` objects. Threshold definitions are
loaded from ``docs/hitl-thresholds.md`` via ``load_thresholds_from_md``.

The 6 PRD-required fixtures exercise:

* continuity hit
* peak-flow-deviation hit
* pour-point hit
* calibration-NSE hit
* no hits (all green)
* mixed hits (continuity + calibration)
"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.hitl.threshold_evaluator import (
    ThresholdHit,
    evaluate,
    load_thresholds_from_md,
)


_DEFAULT_THRESHOLDS = {
    "continuity_error_over_threshold": {
        "severity": "block",
        "measured_key": "continuity.flow_routing",
        "operator": ">",
        "value": 5.0,
        "evidence_path": "06_qa/qa_summary.json",
        "message": "Flow routing continuity error exceeds 5%.",
        "rationale": "<!-- HYDROLOGY-TODO -->",
    },
    "peak_flow_deviation_over_threshold": {
        "severity": "block",
        "measured_key": "peak.deviation_percent",
        "operator": ">",
        "value": 25.0,
        "evidence_path": "06_qa/qa_summary.json",
        "message": "Peak flow deviation exceeds 25%.",
        "rationale": "All clear.",
    },
    "pour_point_suspect": {
        "severity": "warn",
        "measured_key": "pour_point.suspect",
        "operator": "==",
        "value": True,
        "evidence_path": "06_qa/qa_summary.json",
        "message": "Pour point flagged as hydrologically suspect.",
        "rationale": "<!-- HYDROLOGY-TODO -->",
    },
    "calibration_nse_low": {
        "severity": "block",
        "measured_key": "calibration.nse",
        "operator": "<",
        "value": 0.5,
        "evidence_path": "06_qa/qa_summary.json",
        "message": "Calibration NSE below 0.5 — calibration likely unusable.",
        "rationale": "<!-- HYDROLOGY-TODO -->",
    },
}


class EvaluateTests(unittest.TestCase):
    def test_continuity_hit_only(self) -> None:
        qa = {"continuity": {"flow_routing": 6.5}}
        hits = evaluate(qa, _DEFAULT_THRESHOLDS)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertIsInstance(hit, ThresholdHit)
        self.assertEqual(hit.pattern, "continuity_error_over_threshold")
        self.assertEqual(hit.severity, "block")
        self.assertEqual(hit.measured_value, 6.5)
        self.assertEqual(hit.threshold_value, 5.0)
        self.assertEqual(hit.evidence_ref, "06_qa/qa_summary.json")

    def test_peak_flow_deviation_hit(self) -> None:
        qa = {"peak": {"deviation_percent": 42.0}}
        hits = evaluate(qa, _DEFAULT_THRESHOLDS)
        names = [h.pattern for h in hits]
        self.assertEqual(names, ["peak_flow_deviation_over_threshold"])

    def test_pour_point_suspect_hit(self) -> None:
        qa = {"pour_point": {"suspect": True}}
        hits = evaluate(qa, _DEFAULT_THRESHOLDS)
        self.assertEqual([h.pattern for h in hits], ["pour_point_suspect"])
        self.assertEqual(hits[0].severity, "warn")

    def test_calibration_nse_hit(self) -> None:
        qa = {"calibration": {"nse": 0.31}}
        hits = evaluate(qa, _DEFAULT_THRESHOLDS)
        self.assertEqual([h.pattern for h in hits], ["calibration_nse_low"])

    def test_no_hits(self) -> None:
        qa = {
            "continuity": {"flow_routing": 0.1},
            "peak": {"deviation_percent": 5.0},
            "pour_point": {"suspect": False},
            "calibration": {"nse": 0.85},
        }
        self.assertEqual(evaluate(qa, _DEFAULT_THRESHOLDS), [])

    def test_mixed_hits(self) -> None:
        qa = {
            "continuity": {"flow_routing": 7.0},
            "peak": {"deviation_percent": 5.0},
            "pour_point": {"suspect": False},
            "calibration": {"nse": 0.2},
        }
        patterns = sorted(h.pattern for h in evaluate(qa, _DEFAULT_THRESHOLDS))
        self.assertEqual(
            patterns,
            ["calibration_nse_low", "continuity_error_over_threshold"],
        )

    def test_missing_measured_key_does_not_raise(self) -> None:
        # An absent key is not a hit; the evaluator must not raise.
        self.assertEqual(evaluate({}, _DEFAULT_THRESHOLDS), [])

    def test_placeholder_rationale_is_detected_on_hit(self) -> None:
        qa = {"continuity": {"flow_routing": 6.0}}
        hits = evaluate(qa, _DEFAULT_THRESHOLDS)
        self.assertTrue(hits[0].rationale_is_placeholder)


class LoadThresholdsFromMarkdownTests(unittest.TestCase):
    def test_parses_yaml_front_matter(self) -> None:
        body = textwrap.dedent(
            """
            ---
            schema_version: 1
            thresholds:
              continuity_error_over_threshold:
                severity: block
                measured_key: "continuity.flow_routing"
                operator: ">"
                value: 5.0
                evidence_path: "06_qa/qa_summary.json"
                message: "Flow routing continuity error exceeds 5%."
                rationale: "<!-- HYDROLOGY-TODO -->"
              peak_flow_deviation_over_threshold:
                severity: block
                measured_key: "peak.deviation_percent"
                operator: ">"
                value: 25.0
                evidence_path: "06_qa/qa_summary.json"
                message: "Peak flow deviation exceeds 25%."
                rationale: "<!-- HYDROLOGY-TODO -->"
            ---

            # HITL Thresholds
            """
        ).strip()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "hitl-thresholds.md"
            path.write_text(body, encoding="utf-8")
            parsed = load_thresholds_from_md(path)

        self.assertIn("continuity_error_over_threshold", parsed)
        self.assertEqual(
            parsed["continuity_error_over_threshold"]["value"],
            5.0,
        )
        self.assertEqual(
            parsed["peak_flow_deviation_over_threshold"]["operator"],
            ">",
        )

    def test_missing_front_matter_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "hitl-thresholds.md"
            path.write_text("# No YAML here\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_thresholds_from_md(path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
