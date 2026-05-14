"""Smoke test for the Morris branch of sensitivity.py.

Acceptance per #49:
- Run on a 4-parameter fixture.
- `sensitivity_indices.json` contains `mu_star` + `sigma` per parameter.
- Sample budget = r * (k + 1) where r is `--morris-r` and k is the number
  of parameters.

We use a tiny `r` to keep the swmm5 call count low.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INP_FIXTURE = REPO_ROOT / "examples" / "todcreek" / "model_chicago5min.inp"
PATCH_MAP = REPO_ROOT / "examples" / "calibration" / "patch_map.json"
SENSITIVITY_PY = REPO_ROOT / "skills" / "swmm-uncertainty" / "scripts" / "sensitivity.py"


def _has_swmm5() -> bool:
    return shutil.which("swmm5") is not None


def _has_salib() -> bool:
    return importlib.util.find_spec("SALib") is not None


@pytest.mark.skipif(not _has_swmm5(), reason="swmm5 binary not available on PATH")
@pytest.mark.skipif(not _has_salib(), reason="SALib not installed")
class SensitivityMorrisTests(unittest.TestCase):
    """Morris elementary effects produces mu_star/sigma at the right budget."""

    @classmethod
    def setUpClass(cls) -> None:
        if not SENSITIVITY_PY.exists():
            raise unittest.SkipTest(
                "sensitivity.py not yet present; this test guards #49."
            )

    def setUp(self) -> None:
        import tempfile

        self.tmp_root = Path(tempfile.mkdtemp(prefix="sensitivity-morris-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp_root, ignore_errors=True))

        scripts_dir = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from inp_patch import patch_inp_text  # noqa: WPS433

        truth_params = {
            "pct_imperv_s1": 30.0,
            "n_imperv_s1": 0.018,
            "suction_s1": 95.0,
            "ksat_s1": 9.0,
            "imdmax_s1": 0.25,
        }
        patch_map = json.loads(PATCH_MAP.read_text())
        base_text = INP_FIXTURE.read_text(errors="ignore")
        patched = patch_inp_text(base_text, patch_map, truth_params)
        truth_dir = self.tmp_root / "truth"
        truth_dir.mkdir(parents=True, exist_ok=True)
        inp = truth_dir / "model.inp"
        inp.write_text(patched, encoding="utf-8")
        rpt = truth_dir / "model.rpt"
        out = truth_dir / "model.out"
        proc = subprocess.run(
            ["swmm5", str(inp), str(rpt), str(out)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"truth swmm5 failed: {proc.stderr}")
        from swmmtoolbox import swmmtoolbox  # noqa: WPS433

        series = swmmtoolbox.extract(str(out), "node,O1,Total_inflow")
        df = series.reset_index()
        df.columns = ["timestamp", "flow"]
        self.observed = self.tmp_root / "observed.csv"
        df.to_csv(self.observed, index=False)

        # 4-parameter parameter space (issue contract).
        self.parameter_space = self.tmp_root / "parameter_space.json"
        self.parameter_space.write_text(json.dumps({
            "pct_imperv_s1": {"min": 20.0, "max": 40.0, "type": "float"},
            "n_imperv_s1":   {"min": 0.012, "max": 0.024, "type": "float"},
            "suction_s1":    {"min": 80.0, "max": 110.0, "type": "float"},
            "ksat_s1":       {"min": 6.0, "max": 12.0, "type": "float"},
        }, indent=2))

    def test_morris_writes_mu_star_and_sigma_with_correct_budget(self) -> None:
        summary = self.tmp_root / "sensitivity_indices.json"
        r = 3
        cmd = [
            sys.executable,
            str(SENSITIVITY_PY),
            "--method", "morris",
            "--base-inp", str(INP_FIXTURE),
            "--patch-map", str(PATCH_MAP),
            "--parameter-space", str(self.parameter_space),
            "--observed", str(self.observed),
            "--run-root", str(self.tmp_root / "runs"),
            "--summary-json", str(summary),
            "--swmm-node", "O1",
            "--swmm-attr", "Total_inflow",
            "--morris-r", str(r),
            "--seed", "11",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"sensitivity.py --method morris failed:\nSTDOUT\n{proc.stdout}\nSTDERR\n{proc.stderr}",
        )
        self.assertTrue(summary.exists())
        payload = json.loads(summary.read_text())
        self.assertEqual(payload.get("method"), "morris")

        k = 4
        expected_budget = r * (k + 1)
        self.assertEqual(
            payload.get("sample_budget"),
            expected_budget,
            msg=f"Morris budget must equal r*(k+1) = {expected_budget}.",
        )
        # Optional: number of executed trials should also equal the budget.
        if "trials" in payload:
            self.assertEqual(len(payload["trials"]), expected_budget)

        indices = payload.get("indices") or {}
        self.assertEqual(
            set(indices.keys()),
            {"pct_imperv_s1", "n_imperv_s1", "suction_s1", "ksat_s1"},
        )
        for name, row in indices.items():
            self.assertIn("mu_star", row, msg=f"{name} missing mu_star")
            self.assertIn("sigma", row, msg=f"{name} missing sigma")
            self.assertIsInstance(row["mu_star"], (int, float))
            self.assertIsInstance(row["sigma"], (int, float))


if __name__ == "__main__":
    unittest.main()
