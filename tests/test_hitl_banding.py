"""Pure banding math for the graded HITL gate (spec: fuzzy-hitl-gates).

The band model replaces a single crisp cutoff with three severity
bands (low / medium / high) built from three anchors. The contract
pinned here: values inside the fine anchor never leave "low", values
at or beyond the bad anchor are always "high", the centre anchor is
the peak of "medium", and membership ties resolve to the more severe
band. Direction mirrors the axis for higher-is-better metrics.
"""
from __future__ import annotations

import unittest

from agentic_swmm.hitl.banding import Bands, grade


class BandsParsingTests(unittest.TestCase):
    def test_from_spec_returns_none_without_bands(self) -> None:
        self.assertIsNone(Bands.from_spec({"operator": ">", "value": 5.0}))

    def test_from_spec_returns_none_on_bad_ordering(self) -> None:
        spec = {"bands": {"fine": 5.0, "centre": 1.0, "bad": 10.0}}
        self.assertIsNone(Bands.from_spec(spec))

    def test_from_spec_returns_none_on_missing_key(self) -> None:
        spec = {"bands": {"fine": 1.0, "centre": 5.0}}
        self.assertIsNone(Bands.from_spec(spec))

    def test_from_spec_parses_higher_is_better(self) -> None:
        spec = {
            "direction": "higher_is_better",
            "bands": {"fine": 0.65, "centre": 0.5, "bad": 0.3},
        }
        bands = Bands.from_spec(spec)
        self.assertIsNotNone(bands)
        assert bands is not None
        self.assertEqual(bands.direction, "higher_is_better")


class GradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.continuity = Bands.from_spec(
            {"bands": {"fine": 1.0, "centre": 5.0, "bad": 10.0}}
        )
        assert self.continuity is not None

    def test_demo_table(self) -> None:
        # The worked example from the spec, byte-for-byte expectations.
        cases = {
            0.8: "low",
            4.9: "medium",
            5.1: "medium",
            8.0: "high",
            12.0: "high",
        }
        for value, expected in cases.items():
            _, level = grade(value, self.continuity)
            self.assertEqual(level, expected, f"value={value}")

    def test_membership_degrees_near_the_old_cliff(self) -> None:
        m49, _ = grade(4.9, self.continuity)
        m51, _ = grade(5.1, self.continuity)
        self.assertAlmostEqual(m49["medium"], 0.975, places=3)
        self.assertAlmostEqual(m51["medium"], 0.98, places=3)

    def test_tie_resolves_to_more_severe(self) -> None:
        # 7.5 sits exactly between the centre (5) and bad (10) anchors.
        m, level = grade(7.5, self.continuity)
        self.assertAlmostEqual(m["medium"], 0.5)
        self.assertAlmostEqual(m["high"], 0.5)
        self.assertEqual(level, "high")

    def test_monotonic_safety(self) -> None:
        for value in (10.0, 15.0, 100.0):
            _, level = grade(value, self.continuity)
            self.assertEqual(level, "high", f"value={value}")
        for value in (0.0, 0.5, 1.0):
            _, level = grade(value, self.continuity)
            self.assertEqual(level, "low", f"value={value}")

    def test_float_noise_cannot_flip_a_tie(self) -> None:
        # KGE 0.4 against anchors 0.7 / 0.5 / 0.3 is exactly halfway
        # between centre and bad in real arithmetic; binary floats make
        # the raw memberships differ by ~1e-16. The tie must still
        # resolve severe (this also preserves the issue #52 acceptance
        # bullet: KGE 0.4 blocks).
        kge = Bands.from_spec(
            {
                "direction": "higher_is_better",
                "bands": {"fine": 0.7, "centre": 0.5, "bad": 0.3},
            }
        )
        assert kge is not None
        _, level = grade(0.4, kge)
        self.assertEqual(level, "high")

    def test_higher_is_better_mirrors(self) -> None:
        nse = Bands.from_spec(
            {
                "direction": "higher_is_better",
                "bands": {"fine": 0.65, "centre": 0.5, "bad": 0.3},
            }
        )
        assert nse is not None
        self.assertEqual(grade(0.8, nse)[1], "low")
        self.assertEqual(grade(0.45, nse)[1], "medium")
        self.assertEqual(grade(0.1, nse)[1], "high")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
