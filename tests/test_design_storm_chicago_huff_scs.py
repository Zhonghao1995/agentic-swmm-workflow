"""Tests for Round 2 design-storm shapes (Chicago, Huff, SCS Type II).

Contract:
- Each shape conserves total depth: ``sum(intensity * dt_hr) ≈ depth_mm``
  (or the IDF integral for ``chicago_hyetograph(idf_params=...)``).
- Chicago peak lands at the configured ``peak_position``.
- Huff peak lands in the quartile named by the ``quartile`` argument.
- SCS Type II 24-hr peak sits near the midpoint and the 5-min variant
  produces ``1440/5 = 288`` ordinates.
- The CLI wires the new shapes correctly and existing primitive shapes
  continue to work (regression).
"""

from __future__ import annotations

import io
import math
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime.design_storm import (
    DesignStorm,
    chicago_hyetograph,
    huff_hyetograph,
    scs_type_ii_hyetograph,
)
from agentic_swmm.cli import build_parser


def _conserved_depth(storm: DesignStorm) -> float:
    dt_hr = storm.interval_min / 60.0
    return sum(i * dt_hr for i in storm.intensities_mm_per_hr)


def _dispatch(argv: list[str]) -> tuple[int, str, str]:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = int(args.func(args) or 0)
    return rc, out.getvalue(), err.getvalue()


class ChicagoHyetographTests(unittest.TestCase):
    def test_depth_conservation_centred(self) -> None:
        storm = chicago_hyetograph(
            depth_mm=25.0, duration_min=60, peak_position=0.5, interval_min=5
        )
        self.assertAlmostEqual(_conserved_depth(storm), 25.0, places=5)
        self.assertEqual(storm.shape, "chicago")

    def test_depth_conservation_vancouver_peak(self) -> None:
        storm = chicago_hyetograph(
            depth_mm=40.0, duration_min=180, peak_position=0.4, interval_min=5
        )
        self.assertAlmostEqual(_conserved_depth(storm), 40.0, places=4)

    def test_depth_conservation_midwest_peak(self) -> None:
        storm = chicago_hyetograph(
            depth_mm=10.0, duration_min=240, peak_position=0.375, interval_min=5
        )
        self.assertAlmostEqual(_conserved_depth(storm), 10.0, places=4)

    def test_peak_position_centred(self) -> None:
        storm = chicago_hyetograph(
            depth_mm=20.0, duration_min=120, peak_position=0.5, interval_min=5
        )
        n = len(storm.intensities_mm_per_hr)
        peak_idx = storm.intensities_mm_per_hr.index(
            max(storm.intensities_mm_per_hr)
        )
        # Centre means peak around step n/2.
        self.assertLessEqual(abs(peak_idx - n // 2), 1)

    def test_peak_position_front(self) -> None:
        storm = chicago_hyetograph(
            depth_mm=20.0, duration_min=240, peak_position=0.25, interval_min=5
        )
        n = len(storm.intensities_mm_per_hr)
        peak_idx = storm.intensities_mm_per_hr.index(
            max(storm.intensities_mm_per_hr)
        )
        self.assertLessEqual(abs(peak_idx - int(n * 0.25)), 1)

    def test_peak_position_back(self) -> None:
        storm = chicago_hyetograph(
            depth_mm=20.0, duration_min=240, peak_position=0.75, interval_min=5
        )
        n = len(storm.intensities_mm_per_hr)
        peak_idx = storm.intensities_mm_per_hr.index(
            max(storm.intensities_mm_per_hr)
        )
        self.assertLessEqual(abs(peak_idx - int(n * 0.75)), 1)

    def test_idf_construction_produces_storm(self) -> None:
        storm = chicago_hyetograph(
            idf_params={"a": 65.4, "b": 0.08, "c": 0.81},
            duration_min=60,
            peak_position=0.5,
            interval_min=5,
        )
        self.assertEqual(storm.shape, "chicago")
        self.assertEqual(len(storm.intensities_mm_per_hr), 12)
        self.assertEqual(storm.metadata["construction"], "idf_params")
        # Positive depth integral (the IDF curve is positive everywhere).
        self.assertGreater(_conserved_depth(storm), 0.0)

    def test_idf_intensity_increases_toward_peak(self) -> None:
        """Both legs of the Chicago-IDF should be monotone-rising
        toward the peak from the start, and monotone-falling away."""
        storm = chicago_hyetograph(
            idf_params={"a": 65.4, "b": 0.08, "c": 0.81},
            duration_min=120,
            peak_position=0.5,
            interval_min=5,
        )
        ints = storm.intensities_mm_per_hr
        peak_idx = ints.index(max(ints))
        # Leading leg: each step ≥ previous (up to the peak).
        for i in range(1, peak_idx):
            self.assertGreaterEqual(ints[i], ints[i - 1] - 1e-9)
        # Trailing leg: each step ≤ previous (after the peak).
        for i in range(peak_idx + 1, len(ints)):
            self.assertLessEqual(ints[i], ints[i - 1] + 1e-9)

    def test_rejects_idf_and_depth_both(self) -> None:
        with self.assertRaises(ValueError):
            chicago_hyetograph(
                depth_mm=10.0,
                idf_params={"a": 1.0, "b": 0.0, "c": 0.7},
                duration_min=60,
            )

    def test_rejects_missing_inputs(self) -> None:
        with self.assertRaises(ValueError):
            chicago_hyetograph(duration_min=60)

    def test_rejects_peak_position_out_of_bounds(self) -> None:
        with self.assertRaises(ValueError):
            chicago_hyetograph(
                depth_mm=10.0, duration_min=60, peak_position=0.0
            )
        with self.assertRaises(ValueError):
            chicago_hyetograph(
                depth_mm=10.0, duration_min=60, peak_position=1.0
            )

    def test_idf_rejects_missing_keys(self) -> None:
        with self.assertRaises(ValueError):
            chicago_hyetograph(
                idf_params={"a": 1.0, "b": 0.0},  # missing c
                duration_min=60,
            )

    def test_idf_rejects_non_positive_a(self) -> None:
        with self.assertRaises(ValueError):
            chicago_hyetograph(
                idf_params={"a": -1.0, "b": 0.0, "c": 0.7},
                duration_min=60,
            )


class HuffHyetographTests(unittest.TestCase):
    def test_q1_peak_in_first_quartile(self) -> None:
        storm = huff_hyetograph(
            depth_mm=25.0, duration_min=120, quartile=1, interval_min=5
        )
        n = len(storm.intensities_mm_per_hr)
        peak_idx = storm.intensities_mm_per_hr.index(
            max(storm.intensities_mm_per_hr)
        )
        self.assertLess(peak_idx, n // 4 + 1)

    def test_q2_peak_in_second_quartile(self) -> None:
        storm = huff_hyetograph(
            depth_mm=25.0, duration_min=120, quartile=2, interval_min=5
        )
        n = len(storm.intensities_mm_per_hr)
        peak_idx = storm.intensities_mm_per_hr.index(
            max(storm.intensities_mm_per_hr)
        )
        # Peak in [n/4, n/2).
        self.assertGreaterEqual(peak_idx, n // 4 - 1)
        self.assertLess(peak_idx, n // 2 + 1)

    def test_q3_peak_in_third_quartile(self) -> None:
        storm = huff_hyetograph(
            depth_mm=25.0, duration_min=120, quartile=3, interval_min=5
        )
        n = len(storm.intensities_mm_per_hr)
        peak_idx = storm.intensities_mm_per_hr.index(
            max(storm.intensities_mm_per_hr)
        )
        self.assertGreaterEqual(peak_idx, n // 2 - 1)
        self.assertLess(peak_idx, 3 * n // 4 + 1)

    def test_q4_peak_in_fourth_quartile(self) -> None:
        storm = huff_hyetograph(
            depth_mm=25.0, duration_min=120, quartile=4, interval_min=5
        )
        n = len(storm.intensities_mm_per_hr)
        peak_idx = storm.intensities_mm_per_hr.index(
            max(storm.intensities_mm_per_hr)
        )
        self.assertGreaterEqual(peak_idx, 3 * n // 4 - 1)

    def test_huff_conserves_depth(self) -> None:
        for q in (1, 2, 3, 4):
            storm = huff_hyetograph(
                depth_mm=15.0, duration_min=60, quartile=q, interval_min=5
            )
            self.assertAlmostEqual(_conserved_depth(storm), 15.0, places=5)

    def test_huff_tables_sum_to_one(self) -> None:
        """Each Huff cumulative table must reach 1.0 at 100% duration."""
        from agentic_swmm.agent.swmm_runtime.design_storm import _HUFF_CUMULATIVE
        for q in (1, 2, 3, 4):
            self.assertAlmostEqual(_HUFF_CUMULATIVE[q][-1], 1.0, places=6)

    def test_huff_rejects_invalid_quartile(self) -> None:
        with self.assertRaises(ValueError):
            huff_hyetograph(depth_mm=10.0, duration_min=60, quartile=0)
        with self.assertRaises(ValueError):
            huff_hyetograph(depth_mm=10.0, duration_min=60, quartile=5)

    def test_huff_shape_is_huff(self) -> None:
        storm = huff_hyetograph(
            depth_mm=10.0, duration_min=60, quartile=2, interval_min=5
        )
        self.assertEqual(storm.shape, "huff")
        self.assertEqual(storm.metadata["quartile"], 2)


class ScsTypeIIHyetographTests(unittest.TestCase):
    def test_24h_5min_produces_288_intervals(self) -> None:
        """1440 min / 5 min = 288 *intervals*. The hyetograph carries
        one intensity per interval (step-function over time)."""
        storm = scs_type_ii_hyetograph(
            depth_mm=100.0, duration_min=1440, interval_min=5
        )
        self.assertEqual(len(storm.intensities_mm_per_hr), 288)

    def test_24h_depth_conservation(self) -> None:
        storm = scs_type_ii_hyetograph(
            depth_mm=100.0, duration_min=1440, interval_min=5
        )
        self.assertAlmostEqual(_conserved_depth(storm), 100.0, places=4)

    def test_24h_peak_near_midpoint(self) -> None:
        storm = scs_type_ii_hyetograph(
            depth_mm=100.0, duration_min=1440, interval_min=5
        )
        n = len(storm.intensities_mm_per_hr)
        peak_idx = storm.intensities_mm_per_hr.index(
            max(storm.intensities_mm_per_hr)
        )
        # t=12 hr ≈ step 12*60/5 = 144 (0-based); allow ± a few bins
        # since the embedded curve has its steepest jump at t=12.
        self.assertLess(abs(peak_idx - n // 2), 10)

    def test_other_duration_rescaled(self) -> None:
        """The Type-II shape can be applied to non-24-hr durations;
        depth is still conserved."""
        storm = scs_type_ii_hyetograph(
            depth_mm=30.0, duration_min=720, interval_min=5
        )
        self.assertAlmostEqual(_conserved_depth(storm), 30.0, places=4)
        self.assertEqual(len(storm.intensities_mm_per_hr), 144)

    def test_scs_rejects_negative_depth(self) -> None:
        with self.assertRaises(ValueError):
            scs_type_ii_hyetograph(depth_mm=-1.0, duration_min=1440)

    def test_scs_rejects_non_multiple_duration(self) -> None:
        with self.assertRaises(ValueError):
            scs_type_ii_hyetograph(
                depth_mm=10.0, duration_min=63, interval_min=5
            )

    def test_scs_default_duration_is_24h(self) -> None:
        storm = scs_type_ii_hyetograph(depth_mm=50.0)
        self.assertEqual(storm.duration_min, 1440)


class StormCliRound2Tests(unittest.TestCase):
    def test_cli_chicago_depth_writes_dat(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "chicago.dat"
            rc, _, _ = _dispatch(
                [
                    "storm",
                    "--shape",
                    "chicago",
                    "--depth-mm",
                    "25",
                    "--duration-min",
                    "60",
                    "--peak-position",
                    "0.4",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())
            self.assertIn(";;Name", out.read_text())

    def test_cli_chicago_idf_writes_dat(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "chicago_idf.dat"
            rc, _, _ = _dispatch(
                [
                    "storm",
                    "--shape",
                    "chicago",
                    "--idf",
                    "a=65.4,b=0.08,c=0.81",
                    "--duration-min",
                    "60",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())

    def test_cli_huff_writes_dat(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "huff.dat"
            rc, _, _ = _dispatch(
                [
                    "storm",
                    "--shape",
                    "huff",
                    "--quartile",
                    "2",
                    "--depth-mm",
                    "25",
                    "--duration-min",
                    "60",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())

    def test_cli_huff_requires_quartile(self) -> None:
        rc, _, err = _dispatch(
            [
                "storm",
                "--shape",
                "huff",
                "--depth-mm",
                "25",
                "--duration-min",
                "60",
            ]
        )
        self.assertEqual(rc, 1)
        self.assertIn("quartile", err)

    def test_cli_scs_writes_dat(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "scs.dat"
            rc, _, _ = _dispatch(
                [
                    "storm",
                    "--shape",
                    "scs",
                    "--depth-mm",
                    "100",
                    "--duration-min",
                    "1440",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())

    def test_cli_scs_defaults_duration(self) -> None:
        """``--shape scs`` without --duration-min uses 1440."""
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "scs.dat"
            rc, _, _ = _dispatch(
                [
                    "storm",
                    "--shape",
                    "scs",
                    "--depth-mm",
                    "50",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)

    def test_cli_idf_malformed_errors(self) -> None:
        rc, _, err = _dispatch(
            [
                "storm",
                "--shape",
                "chicago",
                "--idf",
                "a=1.0,b=0.0",  # missing c
                "--duration-min",
                "60",
            ]
        )
        self.assertEqual(rc, 1)
        # The error message names the missing key.
        self.assertIn("c", err.lower())

    def test_cli_uniform_regression_still_works(self) -> None:
        """Existing primitive shapes must still dispatch via the
        unchanged CLI signature."""
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "u.dat"
            rc, _, _ = _dispatch(
                [
                    "storm",
                    "--depth-mm",
                    "10",
                    "--duration-min",
                    "60",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())

    def test_cli_triangular_regression(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "t.dat"
            rc, _, _ = _dispatch(
                [
                    "storm",
                    "--shape",
                    "triangular",
                    "--depth-mm",
                    "10",
                    "--duration-min",
                    "60",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)

    def test_cli_front_loaded_regression(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "f.dat"
            rc, _, _ = _dispatch(
                [
                    "storm",
                    "--shape",
                    "front_loaded",
                    "--depth-mm",
                    "10",
                    "--duration-min",
                    "60",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)

    def test_cli_back_loaded_regression(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "b.dat"
            rc, _, _ = _dispatch(
                [
                    "storm",
                    "--shape",
                    "back_loaded",
                    "--depth-mm",
                    "10",
                    "--duration-min",
                    "60",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
