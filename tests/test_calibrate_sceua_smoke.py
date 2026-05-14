"""Smoke test for the SCE-UA calibration strategy.

Per issue #48 acceptance criteria:
- 50-iter SCE-UA on a 1-subcatchment fixture INP finishes < 30s.
- Writes best_params.json + convergence.csv + calibration_summary.json.
- KGE at the best parameter set is strictly greater than KGE at baseline.

The fixture leans on the existing 1-subcatchment Tod Creek demo INP and the
patch map shipped under examples/calibration/. We synthesise the observed
series by running SWMM once at a known "truth" parameter set so the search
has a real optimum to find.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INP_FIXTURE = REPO_ROOT / "examples" / "todcreek" / "model_chicago5min.inp"
PATCH_MAP = REPO_ROOT / "examples" / "calibration" / "patch_map.json"
CALIBRATE_PY = REPO_ROOT / "skills" / "swmm-calibration" / "scripts" / "swmm_calibrate.py"
SCEUA_PY = REPO_ROOT / "skills" / "swmm-calibration" / "scripts" / "sceua.py"


def _has_swmm5() -> bool:
    return shutil.which("swmm5") is not None


def _has_spotpy() -> bool:
    try:
        import spotpy  # noqa: F401
        return True
    except ImportError:
        return False


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not _has_swmm5(), reason="swmm5 binary not available on PATH")
@pytest.mark.skipif(not _has_spotpy(), reason="spotpy not installed")
class SceUaSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        # Load metrics + sceua modules dynamically (mirrors test_metrics_kge.py pattern).
        self.metrics = _load_module(
            "calib_metrics",
            REPO_ROOT / "skills" / "swmm-calibration" / "scripts" / "metrics.py",
        )
        # Make scripts/ importable so sceua.py can do "from metrics import ...".
        scripts_dir = str(REPO_ROOT / "skills" / "swmm-calibration" / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        self.sceua = _load_module("calib_sceua", SCEUA_PY)

        self.tmp_root = Path(self._make_tmp_dir())
        self.run_root = self.tmp_root / "runs"
        self.run_root.mkdir(parents=True, exist_ok=True)

        # Synthesise an observed series by running SWMM once at a "truth"
        # parameter set, then save it as the calibration target.
        self.observed_csv = self.tmp_root / "observed.csv"
        self.truth_params = {
            "pct_imperv_s1": 30.0,
            "n_imperv_s1": 0.018,
            "suction_s1": 95.0,
            "ksat_s1": 9.0,
            "imdmax_s1": 0.25,
        }
        self._write_observed(self.truth_params)

        # The baseline (starting) point is far from truth so SCE-UA has work to do.
        self.baseline_params = {
            "pct_imperv_s1": 20.0,
            "n_imperv_s1": 0.028,
            "suction_s1": 75.0,
            "ksat_s1": 12.0,
            "imdmax_s1": 0.32,
        }
        # Narrow search space around truth — keeps the smoke test deterministic
        # without being so tight that the algorithm can't fail.
        self.search_space = {
            "pct_imperv_s1": {"min": 20.0, "max": 40.0, "type": "float", "precision": 3},
            "n_imperv_s1":   {"min": 0.012, "max": 0.028, "type": "float", "precision": 4},
            "suction_s1":    {"min": 75.0, "max": 115.0, "type": "float", "precision": 3},
            "ksat_s1":       {"min": 6.0, "max": 12.0, "type": "float", "precision": 3},
            "imdmax_s1":     {"min": 0.18, "max": 0.32, "type": "float", "precision": 4},
        }
        self.search_space_path = self.tmp_root / "search_space.json"
        self.search_space_path.write_text(json.dumps(self.search_space, indent=2))

    def _make_tmp_dir(self) -> str:
        import tempfile

        d = tempfile.mkdtemp(prefix="sceua-smoke-")
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        return d

    def _write_observed(self, params: dict) -> None:
        """Patch INP with `params`, run swmm5, extract O1 inflow, save as CSV."""

        from swmmtoolbox import swmmtoolbox

        # Reuse inp_patch through swmm_calibrate's module-level import.
        scripts_dir = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"
        sys.path.insert(0, str(scripts_dir))
        from inp_patch import patch_inp_text  # noqa: WPS433

        patch_map = json.loads(PATCH_MAP.read_text())
        base_text = INP_FIXTURE.read_text(errors="ignore")
        patched_text = patch_inp_text(base_text, patch_map, params)
        truth_dir = self.tmp_root / "truth"
        truth_dir.mkdir(parents=True, exist_ok=True)
        patched_inp = truth_dir / "model.inp"
        patched_inp.write_text(patched_text, encoding="utf-8")
        rpt = truth_dir / "model.rpt"
        out = truth_dir / "model.out"
        proc = subprocess.run(
            ["swmm5", str(patched_inp), str(rpt), str(out)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"swmm5 failed at truth-params run: {proc.stderr}")
        series = swmmtoolbox.extract(str(out), "node,O1,Total_inflow")
        df = series.reset_index()
        df.columns = ["timestamp", "flow"]
        df.to_csv(self.observed_csv, index=False)

    def _kge_at(self, params: dict) -> float:
        """Run SWMM with `params` and return KGE vs the observed series."""

        from swmmtoolbox import swmmtoolbox
        from inp_patch import patch_inp_text  # noqa: WPS433

        patch_map = json.loads(PATCH_MAP.read_text())
        out_dir = self.tmp_root / f"probe-{abs(hash(tuple(sorted(params.items())))) % 10**8}"
        out_dir.mkdir(parents=True, exist_ok=True)
        patched_text = patch_inp_text(INP_FIXTURE.read_text(errors="ignore"), patch_map, params)
        inp = out_dir / "model.inp"
        inp.write_text(patched_text, encoding="utf-8")
        rpt = out_dir / "model.rpt"
        out = out_dir / "model.out"
        proc = subprocess.run(
            ["swmm5", str(inp), str(rpt), str(out)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=f"swmm5 probe failed: {proc.stderr}")
        series = swmmtoolbox.extract(str(out), "node,O1,Total_inflow")
        sim_df = series.reset_index()
        sim_df.columns = ["timestamp", "flow"]
        obs_df = pd.read_csv(self.observed_csv)
        return float(self.metrics.kge(obs_df, sim_df)["kge"])

    def test_sceua_runs_and_improves_over_baseline(self) -> None:
        baseline_kge = self._kge_at(self.baseline_params)

        summary_path = self.run_root / "calibration_summary.json"
        best_params_path = self.run_root / "best_params.json"
        convergence_path = self.run_root / "convergence.csv"

        cmd = [
            sys.executable,
            str(CALIBRATE_PY),
            "search",
            "--base-inp", str(INP_FIXTURE),
            "--patch-map", str(PATCH_MAP),
            "--search-space", str(self.search_space_path),
            "--observed", str(self.observed_csv),
            "--run-root", str(self.run_root / "trials"),
            "--swmm-node", "O1",
            "--swmm-attr", "Total_inflow",
            "--objective", "kge",
            "--summary-json", str(summary_path),
            "--strategy", "sceua",
            "--iterations", "50",
            "--seed", "11",
            "--best-params-out", str(best_params_path),
            "--convergence-csv", str(convergence_path),
        ]

        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.perf_counter() - t0

        self.assertEqual(proc.returncode, 0, msg=f"sceua command failed:\nSTDOUT\n{proc.stdout}\nSTDERR\n{proc.stderr}")
        self.assertLess(elapsed, 30.0, msg=f"SCE-UA smoke exceeded 30s: {elapsed:.2f}s")

        # Artefacts.
        self.assertTrue(summary_path.exists(), msg=f"missing {summary_path}")
        self.assertTrue(best_params_path.exists(), msg=f"missing {best_params_path}")
        self.assertTrue(convergence_path.exists(), msg=f"missing {convergence_path}")

        summary = json.loads(summary_path.read_text())
        self.assertEqual(summary["primary_objective"], "kge")
        self.assertEqual(summary["strategy"], "sceua")
        # KGE on the best trial should beat the (poor) baseline parameter set.
        best_params = json.loads(best_params_path.read_text())
        best_kge = self._kge_at(best_params)
        self.assertGreater(
            best_kge,
            baseline_kge,
            msg=f"SCE-UA did not improve over baseline: best={best_kge:.4f}, baseline={baseline_kge:.4f}",
        )
        # primary_value in the summary should match (approximately) the best KGE.
        self.assertAlmostEqual(summary["primary_value"], best_kge, places=4)


if __name__ == "__main__":
    unittest.main()
