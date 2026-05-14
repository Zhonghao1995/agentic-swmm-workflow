"""Smoke test for the DREAM-ZS Bayesian calibration strategy.

Per issue #53 acceptance criteria:
- ``import spotpy.algorithms.dream`` works.
- 100-iter / 2-chain DREAM-ZS on a 1-subcatchment fixture INP finishes < 60s.
- Writes the 5 expected artefacts under the audit directory:
    * ``posterior_samples.csv``
    * ``best_params.json``
    * ``chain_convergence.json``
    * ``posterior_<param>.png`` for each parameter
    * ``posterior_correlation.png``
- ``posterior_samples.csv`` has ``(chains × post_burnin) > 0`` rows.
- ``chain_convergence.json`` has a numeric Rhat per parameter (may not converge
  in 100 iter; the test only asserts file shape).
- ``calibration_summary.json`` keeps the Slice 1 shape (primary_objective /
  primary_value / kge_decomposition / secondary_metrics / strategy / iterations
  / convergence_trace_ref) plus DREAM-specific posterior summary fields.

Mirrors the synthesis trick from ``test_calibrate_sceua_smoke.py``: run SWMM
once at a "truth" parameter set to generate the observed series, then ask
DREAM-ZS to recover it. We do NOT require KGE-improvement here — the budget
is too small for Bayesian convergence; the test only proves the pipeline
emits well-formed artefacts.
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
DREAM_PY = REPO_ROOT / "skills" / "swmm-calibration" / "scripts" / "dream_zs.py"


def _has_swmm5() -> bool:
    return shutil.which("swmm5") is not None


def _has_spotpy_dream() -> bool:
    try:
        import spotpy.algorithms.dream  # noqa: F401
        return True
    except ImportError:
        return False


def _has_matplotlib() -> bool:
    try:
        import matplotlib  # noqa: F401
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


def test_spotpy_dream_importable() -> None:
    """Acceptance criterion: import spotpy.algorithms.dream works."""

    import spotpy.algorithms.dream  # noqa: F401


@pytest.mark.skipif(not _has_swmm5(), reason="swmm5 binary not available on PATH")
@pytest.mark.skipif(not _has_spotpy_dream(), reason="spotpy.algorithms.dream not importable")
@pytest.mark.skipif(not _has_matplotlib(), reason="matplotlib not installed")
class DreamZsSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        # Make scripts/ importable so dream_zs.py can do "from metrics import ...".
        scripts_dir = str(REPO_ROOT / "skills" / "swmm-calibration" / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        self.metrics = _load_module(
            "calib_metrics_dream",
            REPO_ROOT / "skills" / "swmm-calibration" / "scripts" / "metrics.py",
        )

        self.tmp_root = Path(self._make_tmp_dir())
        # 09_audit/ is the agreed audit folder; we want to make sure DREAM-ZS
        # writes its 5 artefacts there.
        self.audit_dir = self.tmp_root / "09_audit"
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.run_root = self.tmp_root / "trials"
        self.run_root.mkdir(parents=True, exist_ok=True)

        # Synthesise an observed series from a "truth" parameter set.
        self.observed_csv = self.tmp_root / "observed.csv"
        self.truth_params = {
            "pct_imperv_s1": 30.0,
            "n_imperv_s1": 0.018,
            "suction_s1": 95.0,
            "ksat_s1": 9.0,
            "imdmax_s1": 0.25,
        }
        self._write_observed(self.truth_params)

        # Modest search space so the smoke test stays cheap but exercises
        # multi-dim posterior generation.
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

        d = tempfile.mkdtemp(prefix="dream-zs-smoke-")
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        return d

    def _write_observed(self, params: dict) -> None:
        from swmmtoolbox import swmmtoolbox

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

    def test_dream_zs_pipeline_emits_all_artefacts(self) -> None:
        summary_path = self.audit_dir / "calibration_summary.json"
        best_params_path = self.audit_dir / "best_params.json"
        posterior_csv = self.audit_dir / "posterior_samples.csv"
        convergence_json = self.audit_dir / "chain_convergence.json"
        correlation_png = self.audit_dir / "posterior_correlation.png"

        cmd = [
            sys.executable,
            str(CALIBRATE_PY),
            "search",
            "--base-inp", str(INP_FIXTURE),
            "--patch-map", str(PATCH_MAP),
            "--search-space", str(self.search_space_path),
            "--observed", str(self.observed_csv),
            "--run-root", str(self.run_root),
            "--swmm-node", "O1",
            "--swmm-attr", "Total_inflow",
            "--objective", "kge",
            "--summary-json", str(summary_path),
            "--strategy", "dream-zs",
            "--iterations", "100",
            "--seed", "11",
            "--dream-chains", "2",
            "--dream-output-dir", str(self.audit_dir),
            "--best-params-out", str(best_params_path),
        ]

        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.perf_counter() - t0

        self.assertEqual(
            proc.returncode,
            0,
            msg=f"dream-zs command failed:\nSTDOUT\n{proc.stdout}\nSTDERR\n{proc.stderr}",
        )
        self.assertLess(elapsed, 60.0, msg=f"DREAM-ZS smoke exceeded 60s: {elapsed:.2f}s")

        # All five DREAM-ZS artefacts must exist.
        self.assertTrue(posterior_csv.exists(), msg=f"missing {posterior_csv}")
        self.assertTrue(best_params_path.exists(), msg=f"missing {best_params_path}")
        self.assertTrue(convergence_json.exists(), msg=f"missing {convergence_json}")
        self.assertTrue(correlation_png.exists(), msg=f"missing {correlation_png}")
        for name in self.search_space:
            png = self.audit_dir / f"posterior_{name}.png"
            self.assertTrue(png.exists(), msg=f"missing marginal PNG {png}")

        # posterior_samples.csv must have at least chains × post_burnin > 0 rows.
        df = pd.read_csv(posterior_csv)
        self.assertGreater(len(df), 0, msg="posterior_samples.csv is empty")
        # Must include a 'chain' column + per-parameter columns + likelihood.
        for col in ("chain", "likelihood"):
            self.assertIn(col, df.columns, msg=f"posterior_samples.csv missing column {col}")
        for name in self.search_space:
            self.assertIn(name, df.columns, msg=f"posterior_samples.csv missing parameter {name}")

        # chain_convergence.json has Rhat per parameter (numeric).
        convergence = json.loads(convergence_json.read_text())
        self.assertIn("rhat", convergence, msg="chain_convergence.json missing 'rhat'")
        for name in self.search_space:
            self.assertIn(name, convergence["rhat"], msg=f"chain_convergence.json missing rhat for {name}")
            val = convergence["rhat"][name]
            self.assertIsInstance(val, (int, float), msg=f"rhat for {name} must be numeric, got {type(val)}")

        # Summary shape is Slice 1-compatible with DREAM-specific fields.
        summary = json.loads(summary_path.read_text())
        self.assertEqual(summary["primary_objective"], "kge")
        self.assertEqual(summary["strategy"], "dream-zs")
        self.assertIn("primary_value", summary)
        self.assertIsInstance(summary["primary_value"], (int, float))
        self.assertIn("kge_decomposition", summary)
        for key in ("r", "alpha", "beta"):
            self.assertIn(key, summary["kge_decomposition"])
        self.assertIn("secondary_metrics", summary)
        for key in ("nse", "pbias_pct", "rmse", "peak_error_rel", "peak_timing_min"):
            self.assertIn(key, summary["secondary_metrics"])
        self.assertIn("posterior_summary", summary, msg="DREAM-ZS summary missing posterior_summary block")
        self.assertIn("n_chains", summary["posterior_summary"])
        self.assertIn("converged", summary["posterior_summary"])


if __name__ == "__main__":
    unittest.main()
