"""Schema lock-in for the new calibration_summary.json shape.

Per issue #48, the SCE-UA calibration writes a calibration_summary.json with
the following top-level structure:

{
  "primary_objective": "kge",
  "primary_value": 0.78,
  "kge_decomposition": {"r": 0.92, "alpha": 1.05, "beta": 0.97},
  "secondary_metrics": {
    "nse": 0.74, "pbias_pct": -3.2, "rmse": 0.043,
    "peak_error_rel": 0.08, "peak_timing_min": 12
  },
  "strategy": "sceua",
  "iterations": 200,
  "convergence_trace_ref": "convergence.csv"
}

This test verifies the schema-shaping helper (build_calibration_summary)
in scripts/sceua.py produces an object that satisfies that contract.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"
SCEUA_PATH = SCRIPTS_DIR / "sceua.py"


def load_sceua_module():
    # sceua.py uses 'from metrics import ...' / 'from inp_patch import ...'
    # because that is how it is invoked when swmm_calibrate.py runs from
    # scripts/. Make scripts/ importable before loading.
    scripts_dir = str(SCRIPTS_DIR)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("calib_sceua", SCEUA_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {SCEUA_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["calib_sceua"] = module
    spec.loader.exec_module(module)
    return module


# Required top-level keys per issue #48.
REQUIRED_TOP_LEVEL = {
    "primary_objective",
    "primary_value",
    "kge_decomposition",
    "secondary_metrics",
    "strategy",
    "iterations",
    "convergence_trace_ref",
}
REQUIRED_DECOMP_KEYS = {"r", "alpha", "beta"}
REQUIRED_SECONDARY = {"nse", "pbias_pct", "rmse", "peak_error_rel", "peak_timing_min"}


class CalibrationSummarySchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sceua = load_sceua_module()

    def _make_summary(self) -> dict:
        return self.sceua.build_calibration_summary(
            primary_value=0.78,
            kge_decomposition={"r": 0.92, "alpha": 1.05, "beta": 0.97},
            secondary_metrics={
                "nse": 0.74,
                "pbias_pct": -3.2,
                "rmse": 0.043,
                "peak_error_rel": 0.08,
                "peak_timing_min": 12.0,
            },
            iterations=200,
            convergence_trace_ref="convergence.csv",
        )

    def test_top_level_keys_present(self) -> None:
        summary = self._make_summary()
        missing = REQUIRED_TOP_LEVEL - set(summary.keys())
        self.assertFalse(missing, f"Missing required top-level keys: {sorted(missing)}")

    def test_primary_objective_is_kge(self) -> None:
        summary = self._make_summary()
        self.assertEqual(summary["primary_objective"], "kge")

    def test_strategy_is_sceua(self) -> None:
        summary = self._make_summary()
        self.assertEqual(summary["strategy"], "sceua")

    def test_primary_value_is_finite_float_in_range(self) -> None:
        summary = self._make_summary()
        value = summary["primary_value"]
        self.assertIsInstance(value, float)
        self.assertTrue(math.isfinite(value))
        # KGE is defined on (-inf, 1].
        self.assertLessEqual(value, 1.0)

    def test_kge_decomposition_has_r_alpha_beta(self) -> None:
        summary = self._make_summary()
        decomp = summary["kge_decomposition"]
        self.assertIsInstance(decomp, dict)
        missing = REQUIRED_DECOMP_KEYS - set(decomp.keys())
        self.assertFalse(missing, f"Missing decomposition keys: {sorted(missing)}")
        for key in REQUIRED_DECOMP_KEYS:
            self.assertIsInstance(decomp[key], float, f"{key} should be float")
            self.assertTrue(math.isfinite(decomp[key]), f"{key} should be finite")

    def test_secondary_metrics_include_all_five(self) -> None:
        summary = self._make_summary()
        secondary = summary["secondary_metrics"]
        self.assertIsInstance(secondary, dict)
        missing = REQUIRED_SECONDARY - set(secondary.keys())
        self.assertFalse(missing, f"Missing secondary_metrics keys: {sorted(missing)}")

    def test_iterations_is_positive_int(self) -> None:
        summary = self._make_summary()
        self.assertIsInstance(summary["iterations"], int)
        self.assertGreaterEqual(summary["iterations"], 1)

    def test_convergence_trace_ref_is_string(self) -> None:
        summary = self._make_summary()
        self.assertIsInstance(summary["convergence_trace_ref"], str)
        self.assertTrue(summary["convergence_trace_ref"].endswith(".csv"))

    def test_none_secondary_metrics_pass_through(self) -> None:
        """Schema permits None values for any individual secondary metric."""

        summary = self.sceua.build_calibration_summary(
            primary_value=0.5,
            kge_decomposition={"r": 0.8, "alpha": 1.0, "beta": 1.0},
            secondary_metrics={
                "nse": None,
                "pbias_pct": None,
                "rmse": None,
                "peak_error_rel": None,
                "peak_timing_min": None,
            },
            iterations=50,
            convergence_trace_ref="convergence.csv",
        )
        # Still required to declare all five keys, even when value is None.
        missing = REQUIRED_SECONDARY - set(summary["secondary_metrics"].keys())
        self.assertFalse(missing)


if __name__ == "__main__":
    unittest.main()
