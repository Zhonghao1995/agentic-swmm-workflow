"""Tests for the perturbation branch of `rainfall_ensemble.py` (issue #51).

Method A — Time-series perturbation. Four models:
  * `gaussian_iid`        — additive zero-mean Gaussian noise
  * `multiplicative`      — multiplicative log-normal-style noise, preserves shape
  * `autocorrelated`      — AR(1) noise with configurable phi
  * `intensity_scaling`   — variance proportional to intensity (peaks vary more)

Statistical contracts:
  * gaussian:           mean noise per realisation ≈ 0
  * multiplicative:     Pearson correlation between observed and realisation ≈ 1
                        (the shape is preserved, magnitudes are scaled)
  * autocorrelated:     measured AR(1) lag-1 autocorrelation across realisations
                        ≈ specified `ar1_coefficient`
  * intensity_scaling:  variance at peaks > variance at troughs

Plus:
  * `preserve_total_volume=True`  -> per-realisation total rainfall is exactly
                                     the observed total (within rounding)
  * `preserve_total_volume=False` -> total varies across realisations

These tests exercise the Python module directly (not the CLI), so they run
without `swmm5`. The CLI integration test lives in a separate file.
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
    """Shared loader so tests skip if the module isn't there yet."""

    @classmethod
    def setUpClass(cls) -> None:
        if not RAINFALL_PY.exists():
            raise unittest.SkipTest("rainfall_ensemble.py not yet present; this test guards #51.")
        cls.mod = _load_module("rainfall_ensemble_under_test", RAINFALL_PY)


class GaussianPerturbationTests(_RainfallModuleMixin):
    """`gaussian_iid` adds zero-mean noise -> mean residual ≈ 0."""

    def test_mean_noise_close_to_zero(self) -> None:
        rng = np.random.default_rng(7)
        # synthetic observed series: 60 steps of a triangular hyetograph
        n = 60
        observed = np.concatenate([np.linspace(0.0, 10.0, n // 2), np.linspace(10.0, 0.0, n // 2)])
        cfg = {
            "model": "gaussian_iid",
            "sigma": 1.0,
            "preserve_total_volume": False,
        }
        realisations = self.mod.perturb_series(
            observed=observed,
            config=cfg,
            n_realisations=200,
            rng=rng,
        )
        self.assertEqual(realisations.shape, (200, n))
        # Mean of (realisation - observed) across all draws and timesteps
        # is the additive noise. With 200*60 = 12_000 draws sigma_mean ~ 0.009
        # so a 0.1 tolerance is far inside the 6-sigma envelope.
        residual_mean = float(np.mean(realisations - observed))
        self.assertLess(abs(residual_mean), 0.1)

    def test_non_negativity_floor(self) -> None:
        """Rainfall cannot be negative; the perturbed series must be clipped."""
        rng = np.random.default_rng(11)
        observed = np.array([0.0, 0.1, 0.2, 0.3, 0.0])
        cfg = {"model": "gaussian_iid", "sigma": 5.0, "preserve_total_volume": False}
        realisations = self.mod.perturb_series(
            observed=observed,
            config=cfg,
            n_realisations=50,
            rng=rng,
        )
        self.assertTrue(np.all(realisations >= 0.0))


class MultiplicativePerturbationTests(_RainfallModuleMixin):
    """`multiplicative` preserves shape: corr(observed, realisation) ≈ 1."""

    def test_shape_preserved(self) -> None:
        rng = np.random.default_rng(13)
        n = 80
        x = np.linspace(0, np.pi, n)
        observed = (np.sin(x) * 10.0) + 0.001  # strictly positive
        cfg = {"model": "multiplicative", "sigma": 0.10, "preserve_total_volume": False}
        realisations = self.mod.perturb_series(
            observed=observed,
            config=cfg,
            n_realisations=100,
            rng=rng,
        )
        # Each realisation should be ~ scalar(s) * observed pattern (within
        # a multiplicative noise budget). Pearson correlation captures shape.
        for row in realisations:
            corr = float(np.corrcoef(observed, row)[0, 1])
            self.assertGreater(corr, 0.95, msg=f"shape not preserved: corr={corr:.3f}")


class AutocorrelatedPerturbationTests(_RainfallModuleMixin):
    """`autocorrelated` noise has lag-1 autocorrelation ≈ specified phi."""

    def test_ar1_coefficient_recovered(self) -> None:
        rng = np.random.default_rng(17)
        # 4000-step series so lag-1 estimator variance is small.
        observed = np.full(4000, 1.0)
        target_phi = 0.7
        cfg = {
            "model": "autocorrelated",
            "sigma": 0.5,
            "ar1_coefficient": target_phi,
            "preserve_total_volume": False,
        }
        realisations = self.mod.perturb_series(
            observed=observed,
            config=cfg,
            n_realisations=10,
            rng=rng,
        )
        # Estimate phi per realisation, average across realisations.
        phis: list[float] = []
        for row in realisations:
            noise = row - observed
            # lag-1 correlation of `noise`
            num = float(np.sum(noise[:-1] * noise[1:]))
            den = float(np.sum(noise ** 2))
            if den > 0:
                phis.append(num / den)
        self.assertTrue(phis, "no realisations produced AR(1) noise")
        phi_hat = float(np.mean(phis))
        # 4000-step AR(1) phi standard error is roughly (1 - phi^2)/sqrt(N) ~ 0.008
        # so 0.1 is comfortable headroom.
        self.assertLess(
            abs(phi_hat - target_phi),
            0.10,
            msg=f"AR(1) coefficient {phi_hat:.3f} not close to {target_phi}",
        )


class IntensityScalingPerturbationTests(_RainfallModuleMixin):
    """`intensity_scaling` -> peaks vary more than troughs."""

    def test_higher_variance_at_peaks(self) -> None:
        rng = np.random.default_rng(19)
        n = 80
        # triangular hyetograph with a single peak
        observed = np.concatenate([
            np.linspace(0.0, 10.0, n // 2),
            np.linspace(10.0, 0.0, n // 2),
        ])
        cfg = {"model": "intensity_scaling", "sigma": 0.25, "preserve_total_volume": False}
        realisations = self.mod.perturb_series(
            observed=observed,
            config=cfg,
            n_realisations=300,
            rng=rng,
        )
        # variance across realisations at each step
        var_per_step = realisations.var(axis=0)
        peak_idx = int(np.argmax(observed))
        # average variance near peaks vs near troughs (first/last 10%)
        trough_indices = np.r_[0:max(1, n // 10), n - n // 10:n]
        peak_indices = np.r_[max(0, peak_idx - 2): min(n, peak_idx + 3)]
        var_peak = float(var_per_step[peak_indices].mean())
        var_trough = float(var_per_step[trough_indices].mean())
        self.assertGreater(
            var_peak,
            var_trough,
            msg=f"intensity_scaling did not scale variance with intensity (peak={var_peak:.3f}, trough={var_trough:.3f})",
        )


class PreserveTotalVolumeTests(_RainfallModuleMixin):
    """`preserve_total_volume` flag: True -> constant total per realisation."""

    def test_preserve_true_total_constant(self) -> None:
        rng = np.random.default_rng(23)
        n = 60
        observed = np.linspace(0.0, 10.0, n)
        cfg = {"model": "gaussian_iid", "sigma": 0.5, "preserve_total_volume": True}
        realisations = self.mod.perturb_series(
            observed=observed,
            config=cfg,
            n_realisations=50,
            rng=rng,
        )
        totals = realisations.sum(axis=1)
        observed_total = float(observed.sum())
        # within 1% of observed total
        self.assertTrue(
            np.allclose(totals, observed_total, rtol=1e-3),
            msg=f"preserve_total_volume=True: totals not constant ({totals[:5]} vs {observed_total})",
        )

    def test_preserve_false_total_varies(self) -> None:
        rng = np.random.default_rng(29)
        n = 60
        observed = np.linspace(0.0, 10.0, n)
        cfg = {"model": "gaussian_iid", "sigma": 0.5, "preserve_total_volume": False}
        realisations = self.mod.perturb_series(
            observed=observed,
            config=cfg,
            n_realisations=50,
            rng=rng,
        )
        totals = realisations.sum(axis=1)
        observed_total = float(observed.sum())
        # std deviation of totals should be non-trivial (> ~0.5)
        self.assertGreater(
            float(totals.std()),
            0.5,
            msg=f"preserve_total_volume=False: totals look pinned ({totals[:5]})",
        )
        # And mean is still around the observed total (not biased)
        self.assertAlmostEqual(float(totals.mean()), observed_total, delta=observed_total * 0.05)


class RainfallSeriesIOTests(_RainfallModuleMixin):
    """Reading CSV + SWMM .dat rainfall timeseries."""

    def test_read_csv_rainfall(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            csv_path = tmpdir / "rain.csv"
            csv_path.write_text(
                "timestamp,rainfall\n"
                "2024-01-01 00:00:00,0.0\n"
                "2024-01-01 00:05:00,1.5\n"
                "2024-01-01 00:10:00,3.0\n",
                encoding="utf-8",
            )
            series = self.mod.read_rainfall_series(csv_path)
            # The module returns a list[(timestamp, value)] or similar — at
            # minimum it should expose `.values` (np.ndarray) and `.timestamps`.
            self.assertTrue(hasattr(series, "values"))
            self.assertTrue(hasattr(series, "timestamps"))
            self.assertEqual(len(series.values), 3)
            self.assertAlmostEqual(float(series.values[1]), 1.5)


if __name__ == "__main__":
    unittest.main()
