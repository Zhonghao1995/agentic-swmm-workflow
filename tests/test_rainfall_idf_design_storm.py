"""Tests for the IDF design-storm branch of rainfall_ensemble.py (issue #51).

Method B contract:
  * 3 storm types — chicago, huff (4 quartiles), scs_type_ii
  * Each produces a hyetograph at the requested duration + interval
  * Chicago: peak placed at `chicago_peak_position` (default 0.4)
  * Huff: quartile shape places the peak in the requested quartile
  * SCS Type II: canonical 24-hr Type II — peak near hour 12 (mid-day)
  * IDF parameter sampling: hyetograph variance across realisations
    stays inside the input CI bounds (peak intensity range ⊆ CI envelope).

These are pure-Python tests (no swmm5 dependency).
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
RAINFALL_PY = REPO_ROOT / "skills" / "swmm-uncertainty" / "scripts" / "rainfall_ensemble.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _RainfallModuleMixin(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not RAINFALL_PY.exists():
            raise unittest.SkipTest("rainfall_ensemble.py not yet present; this test guards #51.")
        cls.mod = _load_module("rainfall_ensemble_idf_under_test", RAINFALL_PY)


class IdfIntensityTests(_RainfallModuleMixin):
    """`idf_intensity_mm_per_hr` computes i = a / (d_hr + b)^c."""

    def test_idf_canonical_value(self) -> None:
        # i(60 min) = 30 / (1.0 + 0.5)^0.7 = 30 / 1.5^0.7
        a, b, c = 30.0, 0.5, 0.7
        expected = a / ((60 / 60.0 + b) ** c)
        got = self.mod.idf_intensity_mm_per_hr(60, a, b, c)
        self.assertAlmostEqual(got, expected, places=6)


class ChicagoStormTests(_RainfallModuleMixin):
    """Chicago hyetograph has the right duration + peak placement."""

    def test_chicago_duration_and_steps(self) -> None:
        series = self.mod.synthesise_design_hyetograph(
            storm_type="chicago",
            duration_minutes=360,
            interval_minutes=5,
            a=30.0,
            b=0.5,
            c=0.7,
            chicago_peak_position=0.4,
        )
        self.assertEqual(len(series.values), 360 // 5)
        # Total time spanned
        total_minutes = (series.timestamps[-1] - series.timestamps[0]).total_seconds() / 60.0
        self.assertAlmostEqual(total_minutes, 360 - 5, places=6)

    def test_chicago_peak_at_requested_position(self) -> None:
        duration = 360
        interval = 5
        peak_position = 0.4
        series = self.mod.synthesise_design_hyetograph(
            storm_type="chicago",
            duration_minutes=duration,
            interval_minutes=interval,
            a=30.0,
            b=0.5,
            c=0.7,
            chicago_peak_position=peak_position,
        )
        n_steps = duration // interval
        peak_idx = int(np.argmax(series.values))
        expected_idx = int(round(n_steps * peak_position))
        # within 1 step (rounding) of expected
        self.assertLessEqual(
            abs(peak_idx - expected_idx),
            1,
            msg=f"chicago peak at step {peak_idx}, expected ~{expected_idx}",
        )

    def test_chicago_peak_position_default_04(self) -> None:
        duration = 120
        interval = 5
        series = self.mod.synthesise_design_hyetograph(
            storm_type="chicago",
            duration_minutes=duration,
            interval_minutes=interval,
            a=30.0,
            b=0.5,
            c=0.7,
        )
        peak_idx = int(np.argmax(series.values))
        # default 0.4 -> peak around step 9-10 out of 24
        self.assertLessEqual(abs(peak_idx - int(round(0.4 * (duration // interval)))), 1)


class HuffStormTests(_RainfallModuleMixin):
    """Huff hyetographs place the peak in the requested quartile."""

    def test_huff_q1_peaks_in_first_quartile(self) -> None:
        series = self.mod.synthesise_design_hyetograph(
            storm_type="huff",
            duration_minutes=240,
            interval_minutes=5,
            a=30.0,
            b=0.5,
            c=0.7,
            huff_quartile=1,
        )
        n_steps = len(series.values)
        peak_idx = int(np.argmax(series.values))
        # Q1 peak falls within the first 25% of steps
        self.assertLess(peak_idx, n_steps * 0.25 + 1)

    def test_huff_q4_peaks_in_fourth_quartile(self) -> None:
        series = self.mod.synthesise_design_hyetograph(
            storm_type="huff",
            duration_minutes=240,
            interval_minutes=5,
            a=30.0,
            b=0.5,
            c=0.7,
            huff_quartile=4,
        )
        n_steps = len(series.values)
        peak_idx = int(np.argmax(series.values))
        # Q4 peak falls within the last 25% of steps
        self.assertGreater(peak_idx, n_steps * 0.75 - 1)

    def test_huff_total_volume_matches_idf(self) -> None:
        a, b, c = 30.0, 0.5, 0.7
        duration_minutes = 120
        interval_minutes = 5
        series = self.mod.synthesise_design_hyetograph(
            storm_type="huff",
            duration_minutes=duration_minutes,
            interval_minutes=interval_minutes,
            a=a,
            b=b,
            c=c,
            huff_quartile=1,
        )
        # Total depth = sum(intensity * dt_hr)
        dt_hr = interval_minutes / 60.0
        total_depth = float(np.sum(series.values) * dt_hr)
        expected = self.mod.idf_intensity_mm_per_hr(duration_minutes, a, b, c) * (duration_minutes / 60.0)
        self.assertAlmostEqual(total_depth, expected, places=2)


class ScsTypeIIStormTests(_RainfallModuleMixin):
    """SCS Type II canonical mass curve — peak around hour 12 of 24."""

    def test_scs_type_ii_peak_near_midday_for_24hr(self) -> None:
        series = self.mod.synthesise_design_hyetograph(
            storm_type="scs_type_ii",
            duration_minutes=1440,
            interval_minutes=5,
            a=30.0,
            b=0.5,
            c=0.7,
        )
        n_steps = len(series.values)
        peak_idx = int(np.argmax(series.values))
        # SCS Type II concentrates between hours 11.5-12.5 -> ~fraction 0.5
        peak_fraction = peak_idx / float(n_steps)
        self.assertGreater(peak_fraction, 0.45)
        self.assertLess(peak_fraction, 0.55)

    def test_scs_type_ii_total_depth_matches_idf(self) -> None:
        a, b, c = 30.0, 0.5, 0.7
        duration_minutes = 1440  # 24 hr canonical
        interval_minutes = 30
        series = self.mod.synthesise_design_hyetograph(
            storm_type="scs_type_ii",
            duration_minutes=duration_minutes,
            interval_minutes=interval_minutes,
            a=a,
            b=b,
            c=c,
        )
        dt_hr = interval_minutes / 60.0
        total_depth = float(np.sum(series.values) * dt_hr)
        expected = self.mod.idf_intensity_mm_per_hr(duration_minutes, a, b, c) * (duration_minutes / 60.0)
        self.assertAlmostEqual(total_depth, expected, places=2)


class IdfParamSamplingTests(_RainfallModuleMixin):
    """Sampling IDF params produces hyetograph variance bounded by the CI."""

    def test_peak_intensity_varies_within_ci_bounds(self) -> None:
        rng = np.random.default_rng(31)
        idf_cfg = {
            "type": "chicago",
            "duration_minutes": 360,
            "return_period_years": 100,
            "interval_minutes": 5,
            "params": {
                "a": {"value": 30.0, "ci": [27.0, 33.0]},
                "b": {"value": 0.5, "ci": [0.4, 0.6]},
                "c": {"value": 0.7, "ci": [0.65, 0.75]},
            },
        }
        realisations = self.mod.build_idf_realisations(
            idf_config=idf_cfg,
            n_realisations=200,
            rng=rng,
        )
        peaks = np.array([float(r.values.max()) for r in realisations])
        # Each realisation should have non-zero peak
        self.assertTrue(np.all(peaks > 0.0))
        # variance across realisations should be non-trivial — CIs are real
        self.assertGreater(float(peaks.std()), 0.0)
        # 95% of realisations stay within the envelope implied by the CI
        # corners (a/c boundaries dominate the peak magnitude).
        envelope_max = self.mod.synthesise_design_hyetograph(
            storm_type="chicago",
            duration_minutes=360,
            interval_minutes=5,
            a=33.0,
            b=0.4,
            c=0.65,
        ).values.max()
        envelope_min = self.mod.synthesise_design_hyetograph(
            storm_type="chicago",
            duration_minutes=360,
            interval_minutes=5,
            a=27.0,
            b=0.6,
            c=0.75,
        ).values.max()
        # Allow a 25% headroom on each side since IDF params are independent
        # gaussians (rare corner draws happen).
        lo = envelope_min * 0.75
        hi = envelope_max * 1.25
        within = np.mean((peaks >= lo) & (peaks <= hi))
        self.assertGreater(within, 0.90, msg=f"only {within*100:.1f}% of peaks within CI envelope")

    def test_n_realisations_count(self) -> None:
        idf_cfg = {
            "type": "scs_type_ii",
            "duration_minutes": 1440,
            "return_period_years": 50,
            "interval_minutes": 30,
            "params": {
                "a": {"value": 25.0, "ci": [22.0, 28.0]},
                "b": {"value": 0.5, "ci": [0.4, 0.6]},
                "c": {"value": 0.7, "ci": [0.65, 0.75]},
            },
        }
        realisations = self.mod.build_idf_realisations(
            idf_config=idf_cfg,
            n_realisations=37,
            rng=np.random.default_rng(7),
        )
        self.assertEqual(len(realisations), 37)


class EnsembleSummaryTests(_RainfallModuleMixin):
    """`run_ensemble` writes the summary JSON with the right schema."""

    def test_idf_run_writes_summary_dry_run(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            cfg = {
                "method": "idf",
                "idf": {
                    "type": "chicago",
                    "duration_minutes": 60,
                    "return_period_years": 10,
                    "interval_minutes": 5,
                    "params": {
                        "a": {"value": 30.0, "ci": [27.0, 33.0]},
                        "b": {"value": 0.5, "ci": [0.4, 0.6]},
                        "c": {"value": 0.7, "ci": [0.65, 0.75]},
                    },
                },
                "n_realisations": 5,
            }
            payload = self.mod.run_ensemble(
                method="idf",
                config=cfg,
                run_root=tmp_root,
                base_inp=None,
                series_name="TS_RAIN",
                swmm_node="O1",
                seed=42,
                dry_run=True,
            )
            self.assertEqual(payload["method"], "idf")
            self.assertEqual(payload["n_realisations"], 5)
            summary_path = tmp_root / "09_audit" / "rainfall_ensemble_summary.json"
            self.assertTrue(summary_path.exists())
            realisations_dir = tmp_root / "09_audit" / "rainfall_realisations"
            self.assertEqual(len(list(realisations_dir.glob("realisation_*.csv"))), 5)
            # Schema fields
            self.assertIn("rainfall_ensemble_stats", payload)
            self.assertIn("peak_intensity_mm_per_hr", payload["rainfall_ensemble_stats"])
            self.assertIn("total_volume_mm", payload["rainfall_ensemble_stats"])

    def test_perturbation_run_writes_summary_dry_run(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            obs_csv = tmp_root / "obs.csv"
            obs_csv.write_text(
                "timestamp,rainfall\n"
                "2024-01-01 00:00:00,1.0\n"
                "2024-01-01 00:05:00,2.0\n"
                "2024-01-01 00:10:00,3.0\n"
                "2024-01-01 00:15:00,2.0\n"
                "2024-01-01 00:20:00,1.0\n",
                encoding="utf-8",
            )
            cfg = {
                "method": "perturbation",
                "perturbation": {
                    "model": "gaussian_iid",
                    "sigma": 0.5,
                    "preserve_total_volume": False,
                },
                "n_realisations": 6,
                "input_rainfall_path": str(obs_csv),
            }
            payload = self.mod.run_ensemble(
                method="perturbation",
                config=cfg,
                run_root=tmp_root,
                base_inp=None,
                series_name="TS_RAIN",
                swmm_node="O1",
                seed=1,
                dry_run=True,
            )
            self.assertEqual(payload["method"], "perturbation")
            self.assertEqual(payload["n_realisations"], 6)
            self.assertEqual(payload["controls"]["interval_minutes"], 5)
            self.assertEqual(payload["controls"]["observed_n_steps"], 5)


if __name__ == "__main__":
    unittest.main()
