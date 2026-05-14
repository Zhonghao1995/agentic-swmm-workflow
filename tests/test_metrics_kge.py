"""KGE metric + decomposition (r / alpha / beta) tests.

Acceptance (issue #48):
- Perfect-match KGE on synthetic series ~ 1.0.
- Shuffled-observed KGE is negative (correlation collapses).
- Each decomposition component matches its closed-form definition:
    r     = Pearson correlation(sim, obs)
    alpha = std(sim) / std(obs)
    beta  = mean(sim) / mean(obs)
    KGE   = 1 - sqrt((r-1)^2 + (alpha-1)^2 + (beta-1)^2)
"""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
METRICS_PATH = REPO_ROOT / "skills" / "swmm-calibration" / "scripts" / "metrics.py"


def load_metrics_module():
    spec = importlib.util.spec_from_file_location("calib_metrics", METRICS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {METRICS_PATH}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass field resolution can find cls.__module__.
    sys.modules["calib_metrics"] = module
    spec.loader.exec_module(module)
    return module


def _as_frame(timestamps: pd.DatetimeIndex, flow: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame({"timestamp": timestamps, "flow": flow})


def _expected_kge_components(sim: np.ndarray, obs: np.ndarray) -> tuple[float, float, float, float]:
    r = float(np.corrcoef(sim, obs)[0, 1])
    alpha = float(np.std(sim, ddof=0) / np.std(obs, ddof=0))
    beta = float(np.mean(sim) / np.mean(obs))
    kge = 1.0 - math.sqrt((r - 1.0) ** 2 + (alpha - 1.0) ** 2 + (beta - 1.0) ** 2)
    return kge, r, alpha, beta


class KgeBasicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metrics = load_metrics_module()
        self.ts = pd.date_range("1984-05-25 09:00", periods=64, freq="5min")
        rng = np.random.default_rng(7)
        obs = 0.5 + 2.0 * np.sin(np.linspace(0.0, 4.0, len(self.ts)) ** 2)
        obs = obs - obs.min() + 0.1  # keep strictly positive
        self.obs_array = obs
        self.sim_perfect = obs.copy()
        self.sim_noisy = obs + rng.normal(scale=0.05, size=len(obs))

    def test_perfect_match_kge_is_one(self) -> None:
        observed = _as_frame(self.ts, self.obs_array)
        simulated = _as_frame(self.ts, self.sim_perfect)
        result = self.metrics.kge(observed, simulated)
        self.assertIn("kge", result)
        self.assertIn("decomposition", result)
        self.assertAlmostEqual(result["kge"], 1.0, places=6)
        self.assertAlmostEqual(result["decomposition"]["r"], 1.0, places=6)
        self.assertAlmostEqual(result["decomposition"]["alpha"], 1.0, places=6)
        self.assertAlmostEqual(result["decomposition"]["beta"], 1.0, places=6)

    def test_shuffled_observed_yields_negative_kge(self) -> None:
        observed = _as_frame(self.ts, self.obs_array)
        rng = np.random.default_rng(99)
        shuffled = self.obs_array.copy()
        rng.shuffle(shuffled)
        # Compare original simulated (= original obs) against shuffled obs as the new "observed".
        observed_shuffled = _as_frame(self.ts, shuffled)
        simulated = _as_frame(self.ts, self.obs_array)
        result = self.metrics.kge(observed_shuffled, simulated)
        self.assertLess(result["kge"], 0.0)
        # Correlation should be roughly zero after shuffle (well below 1).
        self.assertLess(result["decomposition"]["r"], 0.5)

    def test_decomposition_matches_closed_form(self) -> None:
        observed = _as_frame(self.ts, self.obs_array)
        simulated = _as_frame(self.ts, self.sim_noisy)
        result = self.metrics.kge(observed, simulated)
        kge_expected, r_expected, alpha_expected, beta_expected = _expected_kge_components(
            self.sim_noisy, self.obs_array
        )
        self.assertAlmostEqual(result["kge"], kge_expected, places=6)
        self.assertAlmostEqual(result["decomposition"]["r"], r_expected, places=6)
        self.assertAlmostEqual(result["decomposition"]["alpha"], alpha_expected, places=6)
        self.assertAlmostEqual(result["decomposition"]["beta"], beta_expected, places=6)

    def test_alpha_isolates_variance_ratio(self) -> None:
        scale = 2.5
        observed = _as_frame(self.ts, self.obs_array)
        # Same mean as obs but scaled-around-mean variance.
        sim = (self.obs_array - self.obs_array.mean()) * scale + self.obs_array.mean()
        simulated = _as_frame(self.ts, sim)
        result = self.metrics.kge(observed, simulated)
        self.assertAlmostEqual(result["decomposition"]["alpha"], scale, places=6)
        # Beta should be ~1 because we preserved mean.
        self.assertAlmostEqual(result["decomposition"]["beta"], 1.0, places=6)
        # Correlation is preserved by linear scaling around the mean.
        self.assertAlmostEqual(result["decomposition"]["r"], 1.0, places=6)

    def test_beta_isolates_mean_ratio(self) -> None:
        multiplier = 1.5
        observed = _as_frame(self.ts, self.obs_array)
        sim = self.obs_array * multiplier
        simulated = _as_frame(self.ts, sim)
        result = self.metrics.kge(observed, simulated)
        self.assertAlmostEqual(result["decomposition"]["beta"], multiplier, places=6)
        # Scaling all values by a constant scales std by the same factor.
        self.assertAlmostEqual(result["decomposition"]["alpha"], multiplier, places=6)
        self.assertAlmostEqual(result["decomposition"]["r"], 1.0, places=6)


class KgeScoreFromMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metrics = load_metrics_module()

    def test_compute_metrics_exposes_kge_block(self) -> None:
        ts = pd.date_range("1984-05-25 09:00", periods=24, freq="5min")
        rng = np.random.default_rng(13)
        obs = 0.5 + np.cumsum(rng.normal(scale=0.1, size=len(ts)))
        sim = obs + rng.normal(scale=0.02, size=len(ts))
        observed = _as_frame(ts, obs)
        simulated = _as_frame(ts, sim)
        bundle = self.metrics.compute_metrics(observed, simulated)
        as_dict = bundle.to_dict()
        self.assertIn("kge", as_dict)
        self.assertIn("kge_decomposition", as_dict)
        self.assertIn("r", as_dict["kge_decomposition"])
        self.assertIn("alpha", as_dict["kge_decomposition"])
        self.assertIn("beta", as_dict["kge_decomposition"])

    def test_score_from_metrics_supports_kge_objective(self) -> None:
        ts = pd.date_range("1984-05-25 09:00", periods=12, freq="5min")
        obs = np.linspace(0.5, 1.5, len(ts))
        sim = obs.copy()
        observed = _as_frame(ts, obs)
        simulated = _as_frame(ts, sim)
        bundle = self.metrics.compute_metrics(observed, simulated)
        score = self.metrics.score_from_metrics(bundle, "kge")
        # KGE is maximised at 1; score_from_metrics returns it directly (higher = better).
        self.assertAlmostEqual(score, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
