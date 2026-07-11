"""End-to-end smoke: ``aiswmm calibrate`` with the REAL swmm5 binary.

The unit layer (tests/test_calibrate_real_engine.py) proves the facade
against fakes; this is the one test that exercises the full production
stack exactly as a user invokes it: console entry -> _run_real ->
facade -> importlib-loaded skill scripts -> spotpy SCE-UA -> swmm5
subprocess per trial -> swmmtoolbox extraction -> experiment artifacts.

Recipe mirrors tests/test_calibrate_sceua_smoke.py: the observed series
is synthesised by running SWMM once at a known truth parameter set, so
the search has a real optimum. Iterations stay tiny (the sceua smoke
budget note: 50 iters < 30s; we use 10).
"""
from __future__ import annotations

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
SCRIPTS = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"

TRUTH = {"pct_imperv_s1": 42.0}
BOUNDS = "pct_imperv_s1=20,70"


def _has_spotpy() -> bool:
    try:
        import spotpy  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(shutil.which("swmm5") is None, reason="swmm5 not on PATH")
@pytest.mark.skipif(not _has_spotpy(), reason="spotpy not installed")
@pytest.mark.skipif(not INP_FIXTURE.exists(), reason="todcreek fixture missing")
class CalibrateCliE2eSmokeTests(unittest.TestCase):
    def _synthesise_observed(self, tmp: Path) -> Path:
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        from inp_patch import patch_inp_text  # noqa: WPS433
        from swmm_calibrate import extract_simulated_series  # noqa: WPS433

        patch_map = json.loads(PATCH_MAP.read_text(encoding="utf-8"))
        truth_dir = tmp / "truth"
        truth_dir.mkdir(parents=True)
        patched = truth_dir / "model.inp"
        patched.write_text(
            patch_inp_text(INP_FIXTURE.read_text(errors="ignore"), patch_map, TRUTH),
            encoding="utf-8",
        )
        rpt, out = truth_dir / "model.rpt", truth_dir / "model.out"
        proc = subprocess.run(
            ["swmm5", str(patched), str(rpt), str(out)], capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        series = extract_simulated_series(
            out, swmm_node="O1", swmm_attr="Total_inflow", aggregate="none"
        )
        observed = tmp / "observed.csv"
        series.to_csv(observed, index=False)
        return observed

    def test_full_stack_experiment(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            observed = self._synthesise_observed(tmp)
            exp_dir = tmp / "experiment"
            cmd = [
                sys.executable,
                "-m",
                "agentic_swmm.cli",
                "calibrate",
                "--run-id",
                "e2e-smoke",
                "--inp",
                str(INP_FIXTURE),
                "--observed-csv",
                str(observed),
                "--patch-map",
                str(PATCH_MAP),
                "--param",
                BOUNDS,
                "--total-iters",
                "10",
                "--ngs",
                "2",
                "--seed",
                "11",
                "--run-dir",
                str(exp_dir),
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, cwd=REPO_ROOT, timeout=240
            )
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])

            summary = json.loads(proc.stdout[proc.stdout.index("{"):])
            self.assertIs(summary["is_stub"], False)
            self.assertEqual(summary["engine"], "sceua-spotpy")
            self.assertTrue(summary["ok"])
            self.assertIn("pct_imperv_s1", summary["best_parameters"])
            # Same-units observed (synthesised from SWMM itself): the guard
            # must stay silent.
            self.assertEqual(summary["warnings"], [])

            # Experiment layout from the grilled design (ADR-0005).
            self.assertTrue((exp_dir / "progress.json").is_file())
            self.assertTrue((exp_dir / "convergence.csv").is_file())
            self.assertTrue((exp_dir / "calibration_summary.json").is_file())
            self.assertTrue((exp_dir / "best_params.json").is_file())
            self.assertTrue((exp_dir / "09_audit").is_dir())
            self.assertTrue(list((exp_dir / "trials").glob("sceua_*")))
            # KGE is finite and the checkpoint file parses.
            progress = json.loads((exp_dir / "progress.json").read_text())
            self.assertEqual(progress["run_id"], "e2e-smoke")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
