"""Tests for ``agentic_swmm.memory.watershed_similarity`` (PRD-06 Phase C.1).

The watershed similarity layer exposes three verbs:

- :class:`WatershedAttributes` — frozen attribute bag
- :func:`extract_attributes_from_inp` — minimal INP section reader
- :func:`similarity_score` — weighted L2 -> ``[0, 1]`` squash
- :func:`rank_similar_cases` — top-k ranker over a candidates dict
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.watershed_similarity import (
    WatershedAttributes,
    extract_attributes_from_inp,
    rank_similar_cases,
    similarity_score,
)


_MINIMAL_INP = """\
[OPTIONS]
FLOW_UNITS CMS

[SUBCATCHMENTS]
;;Name           Rain Gage        Outlet           Area     %Imperv  Width    %Slope   CurbLen
;;-------------- ---------------- ---------------- -------- -------- -------- -------- --------
S1               RG1              J1               10.0     50.0     800.0    2.5      0
S2               RG1              J1               5.0      30.0     400.0    1.5      0

[CONDUITS]
;;Name           From Node        To Node          Length     Roughness
;;-------------- ---------------- ---------------- ---------- ----------
C1               J1               O1               1000       0.013
C2               J2               O1               500        0.013

[OUTFALLS]
;;Name           Elevation  Type
;;-------------- ---------- ----------
O1               90         FREE
"""


class WatershedAttributesTests(unittest.TestCase):
    def test_dataclass_is_frozen(self) -> None:
        attrs = WatershedAttributes(
            area_ha=1.0,
            imperv_pct=10.0,
            mean_slope_pct=2.0,
            n_subcatchments=1,
            n_conduits=1,
            n_outfalls=1,
        )
        with self.assertRaises(Exception):
            # frozen dataclass disallows attribute mutation
            attrs.area_ha = 99.0  # type: ignore[misc]

    def test_dominant_landuse_defaults_to_none(self) -> None:
        attrs = WatershedAttributes(
            area_ha=1.0,
            imperv_pct=0.0,
            mean_slope_pct=0.0,
            n_subcatchments=0,
            n_conduits=0,
            n_outfalls=0,
        )
        self.assertIsNone(attrs.dominant_landuse)


class ExtractFromInpTests(unittest.TestCase):
    def _write_inp(self, body: str) -> Path:
        tmp = Path(self._tmp.name) / "model.inp"
        tmp.write_text(body, encoding="utf-8")
        return tmp

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_counts_match_sections(self) -> None:
        inp = self._write_inp(_MINIMAL_INP)
        attrs = extract_attributes_from_inp(inp)
        self.assertEqual(attrs.n_subcatchments, 2)
        self.assertEqual(attrs.n_conduits, 2)
        self.assertEqual(attrs.n_outfalls, 1)

    def test_area_is_sum_of_subcatchment_areas(self) -> None:
        inp = self._write_inp(_MINIMAL_INP)
        attrs = extract_attributes_from_inp(inp)
        self.assertAlmostEqual(attrs.area_ha, 15.0, places=5)

    def test_imperv_is_area_weighted_mean(self) -> None:
        # (50*10 + 30*5) / 15 = 43.333...
        inp = self._write_inp(_MINIMAL_INP)
        attrs = extract_attributes_from_inp(inp)
        self.assertAlmostEqual(attrs.imperv_pct, (50 * 10 + 30 * 5) / 15, places=4)

    def test_slope_is_area_weighted_mean(self) -> None:
        # (2.5*10 + 1.5*5) / 15
        inp = self._write_inp(_MINIMAL_INP)
        attrs = extract_attributes_from_inp(inp)
        expected = (2.5 * 10 + 1.5 * 5) / 15
        self.assertAlmostEqual(attrs.mean_slope_pct, expected, places=4)

    def test_missing_sections_yield_zero_counts(self) -> None:
        inp = self._write_inp("[OPTIONS]\nFLOW_UNITS CMS\n")
        attrs = extract_attributes_from_inp(inp)
        self.assertEqual(attrs.n_subcatchments, 0)
        self.assertEqual(attrs.n_conduits, 0)
        self.assertEqual(attrs.n_outfalls, 0)
        self.assertEqual(attrs.area_ha, 0.0)
        self.assertEqual(attrs.imperv_pct, 0.0)
        self.assertEqual(attrs.mean_slope_pct, 0.0)

    def test_truncated_rows_skipped(self) -> None:
        body = (
            "[SUBCATCHMENTS]\n"
            "S1               RG1              J1\n"  # only 3 cols
            "S2               RG1              J1               10.0     30.0     400.0    1.5      0\n"
        )
        inp = self._write_inp(body)
        attrs = extract_attributes_from_inp(inp)
        # Only S2 contributes to the weighted means.
        self.assertEqual(attrs.n_subcatchments, 2)
        self.assertAlmostEqual(attrs.area_ha, 10.0, places=4)
        self.assertAlmostEqual(attrs.imperv_pct, 30.0, places=4)
        self.assertAlmostEqual(attrs.mean_slope_pct, 1.5, places=4)

    def test_nonnumeric_area_tolerated(self) -> None:
        body = (
            "[SUBCATCHMENTS]\n"
            "S1               RG1              J1               BAD      30       400      2.0      0\n"
            "S2               RG1              J1               5.0      40       400      3.0      0\n"
        )
        inp = self._write_inp(body)
        attrs = extract_attributes_from_inp(inp)
        # Only the second row contributes.
        self.assertAlmostEqual(attrs.area_ha, 5.0, places=4)
        self.assertAlmostEqual(attrs.imperv_pct, 40.0, places=4)

    def test_missing_file_returns_zero_attributes(self) -> None:
        attrs = extract_attributes_from_inp(Path(self._tmp.name) / "does_not_exist.inp")
        self.assertEqual(attrs.n_subcatchments, 0)
        self.assertEqual(attrs.area_ha, 0.0)


class SimilarityScoreTests(unittest.TestCase):
    def _attrs(self, **over: float | int) -> WatershedAttributes:
        defaults = dict(
            area_ha=10.0,
            imperv_pct=30.0,
            mean_slope_pct=2.0,
            n_subcatchments=20,
            n_conduits=18,
            n_outfalls=1,
        )
        defaults.update(over)
        return WatershedAttributes(**defaults)  # type: ignore[arg-type]

    def test_identical_inputs_score_one(self) -> None:
        a = self._attrs()
        b = self._attrs()
        self.assertAlmostEqual(similarity_score(a, b), 1.0, places=10)

    def test_score_is_symmetric(self) -> None:
        a = self._attrs()
        b = self._attrs(area_ha=50.0, imperv_pct=80.0)
        self.assertAlmostEqual(similarity_score(a, b), similarity_score(b, a), places=10)

    def test_score_in_unit_interval(self) -> None:
        a = self._attrs()
        b = self._attrs(area_ha=1e6, imperv_pct=99.0, n_subcatchments=10000)
        s = similarity_score(a, b)
        self.assertGreater(s, 0.0)
        self.assertLessEqual(s, 1.0)

    def test_closer_attributes_score_higher(self) -> None:
        # close vs far on every dimension
        target = self._attrs()
        close = self._attrs(area_ha=11.0, imperv_pct=32.0)
        far = self._attrs(
            area_ha=1000.0, imperv_pct=90.0, mean_slope_pct=15.0, n_subcatchments=500
        )
        self.assertGreater(similarity_score(target, close), similarity_score(target, far))

    def test_log_area_handles_order_of_magnitude_difference(self) -> None:
        # 1 ha vs 1000 ha: log-space difference is bounded, score does
        # not collapse to ~0.
        a = self._attrs(area_ha=1.0)
        b = self._attrs(area_ha=1000.0)
        self.assertGreater(similarity_score(a, b), 0.4)


class RankSimilarCasesTests(unittest.TestCase):
    def _attrs(self, area_ha: float, imperv: float) -> WatershedAttributes:
        return WatershedAttributes(
            area_ha=area_ha,
            imperv_pct=imperv,
            mean_slope_pct=2.0,
            n_subcatchments=20,
            n_conduits=18,
            n_outfalls=1,
        )

    def test_top_k_sorted_descending(self) -> None:
        target = self._attrs(10.0, 30.0)
        candidates = {
            "twin": self._attrs(10.0, 30.0),
            "near": self._attrs(12.0, 35.0),
            "far": self._attrs(200.0, 90.0),
        }
        ranked = rank_similar_cases(target, candidates, top_k=3)
        self.assertEqual([n for n, _ in ranked], ["twin", "near", "far"])
        scores = [s for _, s in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_top_k_truncates(self) -> None:
        target = self._attrs(10.0, 30.0)
        candidates = {
            f"c{i}": self._attrs(10.0 + i, 30.0 + i) for i in range(10)
        }
        ranked = rank_similar_cases(target, candidates, top_k=3)
        self.assertEqual(len(ranked), 3)

    def test_top_k_zero_returns_empty(self) -> None:
        target = self._attrs(10.0, 30.0)
        candidates = {"a": self._attrs(10.0, 30.0)}
        self.assertEqual(rank_similar_cases(target, candidates, top_k=0), [])

    def test_empty_candidates_returns_empty(self) -> None:
        target = self._attrs(10.0, 30.0)
        self.assertEqual(rank_similar_cases(target, {}, top_k=5), [])

    def test_tie_broken_lexicographically(self) -> None:
        target = self._attrs(10.0, 30.0)
        # Two identical candidates -> tie score; lexicographic order wins.
        candidates = {
            "zeta": self._attrs(10.0, 30.0),
            "alpha": self._attrs(10.0, 30.0),
            "mu": self._attrs(10.0, 30.0),
        }
        ranked = rank_similar_cases(target, candidates, top_k=3)
        self.assertEqual([n for n, _ in ranked], ["alpha", "mu", "zeta"])


if __name__ == "__main__":
    unittest.main()
