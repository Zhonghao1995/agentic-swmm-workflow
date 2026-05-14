"""Smoke test for the Sobol' branch of sensitivity.py.

Acceptance per #49:
- Run on a 4-parameter fixture.
- `sensitivity_indices.json` contains `S_i` (first-order) + `S_T_i`
  (total-effect) per parameter.
- Sample budget = N * (2k + 2) where N is `--sobol-n` and k is the number
  of parameters.
- Sanity cross-check: top-1 ranked parameter (by S_T_i) agrees with the
  top-1 from Morris (by mu_star) on the same fixture.

We use a tiny `N` (= 4) so the budget stays small.
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
class SensitivitySobolTests(unittest.TestCase):
    """Sobol' indices include first-order + total-effect with correct budget."""

    @classmethod
    def setUpClass(cls) -> None:
        if not SENSITIVITY_PY.exists():
            raise unittest.SkipTest(
                "sensitivity.py not yet present; this test guards #49."
            )

    def setUp(self) -> None:
        import tempfile

        self.tmp_root = Path(tempfile.mkdtemp(prefix="sensitivity-sobol-"))
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

        # 4-parameter parameter space (same as Morris test fixture).
        self.parameter_space = self.tmp_root / "parameter_space.json"
        self.parameter_space.write_text(json.dumps({
            "pct_imperv_s1": {"min": 20.0, "max": 40.0, "type": "float"},
            "n_imperv_s1":   {"min": 0.012, "max": 0.024, "type": "float"},
            "suction_s1":    {"min": 80.0, "max": 110.0, "type": "float"},
            "ksat_s1":       {"min": 6.0, "max": 12.0, "type": "float"},
        }, indent=2))

    def _run_sa(self, method: str, **extra) -> dict:
        summary = self.tmp_root / f"sensitivity_{method}.json"
        cmd = [
            sys.executable,
            str(SENSITIVITY_PY),
            "--method", method,
            "--base-inp", str(INP_FIXTURE),
            "--patch-map", str(PATCH_MAP),
            "--parameter-space", str(self.parameter_space),
            "--observed", str(self.observed),
            "--run-root", str(self.tmp_root / f"runs-{method}"),
            "--summary-json", str(summary),
            "--swmm-node", "O1",
            "--swmm-attr", "Total_inflow",
            "--seed", "11",
        ]
        for k, v in extra.items():
            cmd.extend([k, str(v)])
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"sensitivity.py --method {method} failed:\nSTDOUT\n{proc.stdout}\nSTDERR\n{proc.stderr}",
        )
        self.assertTrue(summary.exists())
        return json.loads(summary.read_text())

    def test_sobol_indices_and_budget_and_agree_with_morris(self) -> None:
        # Sobol on tiny N.
        N = 4
        sobol_payload = self._run_sa("sobol", **{"--sobol-n": N})
        self.assertEqual(sobol_payload.get("method"), "sobol")

        k = 4
        expected_budget = N * (2 * k + 2)
        self.assertEqual(
            sobol_payload.get("sample_budget"),
            expected_budget,
            msg=f"Sobol budget must equal N*(2k+2) = {expected_budget}.",
        )
        if "trials" in sobol_payload:
            self.assertEqual(len(sobol_payload["trials"]), expected_budget)

        indices = sobol_payload.get("indices") or {}
        self.assertEqual(
            set(indices.keys()),
            {"pct_imperv_s1", "n_imperv_s1", "suction_s1", "ksat_s1"},
        )
        for name, row in indices.items():
            self.assertIn("S_i", row, msg=f"{name} missing S_i (first-order)")
            self.assertIn("S_T_i", row, msg=f"{name} missing S_T_i (total-effect)")
            self.assertIsInstance(row["S_i"], (int, float))
            self.assertIsInstance(row["S_T_i"], (int, float))

        # Morris on same fixture, then sanity-check top-1.
        morris_payload = self._run_sa(
            "morris",
            **{"--morris-r": 3},
        )
        morris_indices = morris_payload.get("indices") or {}
        morris_top = max(
            morris_indices.items(),
            key=lambda kv: abs(kv[1]["mu_star"]),
        )[0]
        sobol_top = max(
            indices.items(),
            key=lambda kv: abs(kv[1]["S_T_i"]),
        )[0]
        self.assertEqual(
            sobol_top,
            morris_top,
            msg=(
                "Top-1 ranked parameter must agree between Morris (mu_star) "
                f"and Sobol' (S_T_i); got Morris={morris_top}, Sobol={sobol_top}."
            ),
        )


if __name__ == "__main__":
    unittest.main()
