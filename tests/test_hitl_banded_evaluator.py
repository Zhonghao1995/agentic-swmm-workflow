"""Graded evaluation in the threshold evaluator (spec: fuzzy-hitl-gates).

An entry that declares a ``bands`` block is graded low / medium / high
instead of the crisp comparison: low is not a hit, medium is a warn
hit, high is a block hit, and the hit carries the memberships, the
anchors, and the direction so the audit record can show why. Entries
without bands (and entries whose bands are malformed) keep the crisp
behavior byte-for-byte.
"""
from __future__ import annotations

import unittest

from agentic_swmm.hitl.threshold_evaluator import evaluate


def _banded_continuity(bands: dict | None) -> dict:
    spec = {
        "severity": "block",
        "measured_key": "continuity.flow_routing",
        "operator": ">",
        "value": 5.0,
        "evidence_path": "07_qa/qa_summary.json",
        "message": "Flow routing continuity error in the uncertain band.",
        "rationale": "All clear.",
    }
    if bands is not None:
        spec["bands"] = bands
    return {"continuity_error_over_threshold": spec}


_BANDS = {"fine": 1.0, "centre": 5.0, "bad": 10.0}


class BandedEvaluateTests(unittest.TestCase):
    def _hits(self, value: float, thresholds: dict | None = None):
        qa = {"continuity": {"flow_routing": value}}
        return evaluate(qa, thresholds or _banded_continuity(_BANDS))

    def test_low_band_is_not_a_hit(self) -> None:
        self.assertEqual(self._hits(0.8), [])

    def test_medium_band_is_a_warn_hit_with_fields(self) -> None:
        hits = self._hits(4.9)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.severity, "warn")
        self.assertEqual(hit.level, "medium")
        assert hit.memberships is not None
        self.assertAlmostEqual(hit.memberships["medium"], 0.975, places=3)
        self.assertEqual(hit.bands, _BANDS)
        self.assertEqual(hit.direction, "higher_is_worse")

    def test_high_band_is_a_block_hit(self) -> None:
        hits = self._hits(12.0)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].severity, "block")
        self.assertEqual(hits[0].level, "high")

    def test_bands_replace_the_crisp_comparison(self) -> None:
        # 4.9 would NOT crisp-hit (> 5.0 is false) but grades medium;
        # conversely a banded entry never applies the operator at all.
        self.assertEqual(self._hits(4.9)[0].level, "medium")

    def test_malformed_bands_fall_back_to_crisp(self) -> None:
        bad_bands = {"fine": 5.0, "centre": 1.0, "bad": 10.0}  # bad ordering
        hits = self._hits(6.5, _banded_continuity(bad_bands))
        self.assertEqual(len(hits), 1)
        self.assertIsNone(hits[0].level)
        self.assertEqual(hits[0].severity, "block")

    def test_entries_without_bands_keep_legacy_none_fields(self) -> None:
        hits = self._hits(6.5, _banded_continuity(None))
        self.assertEqual(len(hits), 1)
        self.assertIsNone(hits[0].level)
        self.assertIsNone(hits[0].memberships)
        self.assertIsNone(hits[0].bands)
        self.assertIsNone(hits[0].direction)

    def test_boolean_measured_never_grades(self) -> None:
        thresholds = {
            "pour_point_suspect": {
                "severity": "warn",
                "measured_key": "pour_point.suspect",
                "operator": "==",
                "value": True,
                "evidence_path": "07_qa/qa_summary.json",
                "message": "Pour point suspect.",
                "rationale": "All clear.",
                # Even a (nonsensical) bands block must not grade a bool.
                "bands": _BANDS,
            }
        }
        hits = evaluate({"pour_point": {"suspect": True}}, thresholds)
        self.assertEqual(len(hits), 1)
        self.assertIsNone(hits[0].level)


class LoaderRobustnessTests(unittest.TestCase):
    def test_malformed_front_matter_raises_value_error(self) -> None:
        # The audit seam catches ValueError; a leaking yaml.YAMLError
        # would crash `aiswmm audit` on a hand-edit typo in the
        # thresholds doc (standards review, quiet-on-malformed rule).
        import tempfile
        from pathlib import Path

        from agentic_swmm.hitl.threshold_evaluator import load_thresholds_from_md

        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "thresholds.md"
            doc.write_text(
                "---\nthresholds:\n  broken: [unclosed\n---\n", encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_thresholds_from_md(doc)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
