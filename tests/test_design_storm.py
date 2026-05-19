"""Tests for ``agentic_swmm.agent.swmm_runtime.design_storm`` (PRD-06 B.4).

Contract:
- ``generate_design_storm`` produces a hyetograph at the requested
  interval with conserved depth (``sum(i * dt_hr) == depth_mm``) for
  every supported shape.
- Triangular peaks land at the correct fractional position.
- ``to_swmm_dat`` emits a header + one row per interval in SWMM's
  ``[TIMESERIES]`` format.
"""

from __future__ import annotations

import unittest

from agentic_swmm.agent.swmm_runtime.design_storm import (
    DesignStorm,
    generate_design_storm,
    to_swmm_dat,
)


def _conserved_depth(storm: DesignStorm) -> float:
    dt_hr = storm.interval_min / 60.0
    return sum(i * dt_hr for i in storm.intensities_mm_per_hr)


class UniformShapeTests(unittest.TestCase):
    def test_uniform_conserves_depth(self) -> None:
        storm = generate_design_storm(
            depth_mm=25.0, duration_min=60, shape="uniform", interval_min=5
        )
        self.assertAlmostEqual(_conserved_depth(storm), 25.0, places=6)
        self.assertEqual(storm.shape, "uniform")
        # Uniform shape: every intensity is equal.
        self.assertEqual(len(set(round(i, 6) for i in storm.intensities_mm_per_hr)), 1)

    def test_uniform_step_count_matches_interval(self) -> None:
        storm = generate_design_storm(
            depth_mm=10.0, duration_min=60, interval_min=5
        )
        self.assertEqual(len(storm.intensities_mm_per_hr), 12)
        self.assertEqual(len(storm.times), 12)


class TriangularShapeTests(unittest.TestCase):
    def test_triangular_conserves_depth(self) -> None:
        storm = generate_design_storm(
            depth_mm=25.0,
            duration_min=60,
            shape="triangular",
            interval_min=5,
        )
        self.assertAlmostEqual(_conserved_depth(storm), 25.0, places=6)

    def test_triangular_peak_at_midpoint(self) -> None:
        storm = generate_design_storm(
            depth_mm=20.0,
            duration_min=120,
            shape="triangular",
            interval_min=5,
        )
        n = len(storm.intensities_mm_per_hr)
        peak_idx = storm.intensities_mm_per_hr.index(max(storm.intensities_mm_per_hr))
        # 12 intervals at 5min over 120min -> n=24, peak should be
        # ~step 11-12 (zero-based) which is ~midpoint.
        self.assertLessEqual(abs(peak_idx - (n // 2 - 1)), 1)


class FrontLoadedShapeTests(unittest.TestCase):
    def test_front_loaded_conserves_depth(self) -> None:
        storm = generate_design_storm(
            depth_mm=12.0,
            duration_min=120,
            shape="front_loaded",
            interval_min=5,
        )
        self.assertAlmostEqual(_conserved_depth(storm), 12.0, places=6)

    def test_front_loaded_peak_at_25_percent(self) -> None:
        storm = generate_design_storm(
            depth_mm=20.0,
            duration_min=240,
            shape="front_loaded",
            interval_min=5,
        )
        n = len(storm.intensities_mm_per_hr)
        peak_idx = storm.intensities_mm_per_hr.index(max(storm.intensities_mm_per_hr))
        expected = int(round(n * 0.25))
        # within one step
        self.assertLessEqual(abs(peak_idx - expected), 1)


class BackLoadedShapeTests(unittest.TestCase):
    def test_back_loaded_peak_at_75_percent(self) -> None:
        storm = generate_design_storm(
            depth_mm=20.0,
            duration_min=240,
            shape="back_loaded",
            interval_min=5,
        )
        n = len(storm.intensities_mm_per_hr)
        peak_idx = storm.intensities_mm_per_hr.index(max(storm.intensities_mm_per_hr))
        expected = int(round(n * 0.75))
        self.assertLessEqual(abs(peak_idx - expected), 1)


class ShapeValidationTests(unittest.TestCase):
    def test_unknown_shape_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_design_storm(
                depth_mm=10.0, duration_min=60, shape="quadratic"
            )

    def test_duration_must_be_multiple_of_interval(self) -> None:
        with self.assertRaises(ValueError):
            generate_design_storm(
                depth_mm=10.0, duration_min=63, interval_min=5
            )

    def test_negative_depth_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_design_storm(depth_mm=-1.0, duration_min=60)

    def test_zero_duration_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_design_storm(depth_mm=10.0, duration_min=0)


class SwmmDatFormatTests(unittest.TestCase):
    def test_to_swmm_dat_one_row_per_interval(self) -> None:
        storm = generate_design_storm(
            depth_mm=25.0, duration_min=60, interval_min=5
        )
        text = to_swmm_dat(storm, station_id="RAIN1")
        # Strip comment rows starting with ;;
        data_rows = [
            line for line in text.splitlines() if line and not line.startswith(";;")
        ]
        self.assertEqual(len(data_rows), len(storm.intensities_mm_per_hr))
        for row in data_rows:
            self.assertTrue(row.startswith("RAIN1"))

    def test_to_swmm_dat_uses_default_station_id(self) -> None:
        storm = generate_design_storm(
            depth_mm=10.0, duration_min=30, interval_min=5
        )
        text = to_swmm_dat(storm)
        data_rows = [
            line for line in text.splitlines() if line and not line.startswith(";;")
        ]
        self.assertTrue(all(line.startswith("STN1") for line in data_rows))


class CliSmokeTests(unittest.TestCase):
    def test_cli_storm_writes_dat_file(self) -> None:
        import tempfile
        from pathlib import Path

        from agentic_swmm.cli import build_parser

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "storm.dat"
            parser = build_parser()
            args = parser.parse_args(
                [
                    "storm",
                    "--depth-mm",
                    "25",
                    "--duration-min",
                    "60",
                    "--shape",
                    "triangular",
                    "--interval-min",
                    "5",
                    "--out",
                    str(out),
                ]
            )
            rc = args.func(args)
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())
            text = out.read_text()
            # Should contain SWMM timeseries header markers
            self.assertIn(";;Name", text)


if __name__ == "__main__":
    unittest.main()
