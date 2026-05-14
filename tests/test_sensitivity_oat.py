"""Regression test for the OAT branch of sensitivity.py.

Slice 4 (#49) ports `swmm-calibration/scripts/parameter_scout.py` to
`swmm-uncertainty/scripts/sensitivity.py --method oat`. The contract is
preserved: same inputs (base-inp, patch-map, base-params, scan-spec,
observed series) -> same ranked output (best trial per parameter, an
importance score, a recommended direction, a narrowed next range).

We synthesise an observed series by running SWMM at a known "truth"
parameter set so the regression has a real-valued optimum.
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


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not _has_swmm5(), reason="swmm5 binary not available on PATH")
class SensitivityOatTests(unittest.TestCase):
    """OAT sensitivity preserves the parameter_scout contract."""

    @classmethod
    def setUpClass(cls) -> None:
        if not SENSITIVITY_PY.exists():
            raise unittest.SkipTest(
                "sensitivity.py not yet present; this test guards the move."
            )

    def setUp(self) -> None:
        import tempfile

        self.tmp_root = Path(tempfile.mkdtemp(prefix="sensitivity-oat-"))
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

        # Baseline + scan spec: same shape parameter_scout consumed.
        self.base_params_path = self.tmp_root / "base_params.json"
        self.base_params_path.write_text(json.dumps({
            "pct_imperv_s1": 20.0,
            "n_imperv_s1": 0.028,
            "suction_s1": 75.0,
            "ksat_s1": 12.0,
            "imdmax_s1": 0.32,
        }, indent=2))
        # Only two parameters scanned -> keeps swmm5 calls small.
        self.scan_spec_path = self.tmp_root / "scan_spec.json"
        self.scan_spec_path.write_text(json.dumps({
            "pct_imperv_s1": [20.0, 30.0, 40.0],
            "n_imperv_s1":  [0.012, 0.018, 0.024],
        }, indent=2))

    def test_oat_writes_ranked_parameter_list_compatible_with_old_scout(self) -> None:
        summary = self.tmp_root / "sensitivity_indices.json"
        cmd = [
            sys.executable,
            str(SENSITIVITY_PY),
            "--method", "oat",
            "--base-inp", str(INP_FIXTURE),
            "--patch-map", str(PATCH_MAP),
            "--base-params", str(self.base_params_path),
            "--scan-spec", str(self.scan_spec_path),
            "--observed", str(self.observed),
            "--run-root", str(self.tmp_root / "runs"),
            "--summary-json", str(summary),
            "--swmm-node", "O1",
            "--swmm-attr", "Total_inflow",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"sensitivity.py --method oat failed:\nSTDOUT\n{proc.stdout}\nSTDERR\n{proc.stderr}",
        )
        self.assertTrue(summary.exists())
        payload = json.loads(summary.read_text())
        # The schema must match the legacy parameter_scout output so downstream
        # consumers (the calibration scaffold) keep working.
        self.assertEqual(payload.get("method"), "oat")
        self.assertIn("parameters", payload)
        names = {row["parameter"] for row in payload["parameters"]}
        self.assertEqual(names, {"pct_imperv_s1", "n_imperv_s1"})
        for row in payload["parameters"]:
            self.assertIn("importance", row)
            self.assertIn("recommended_direction", row)
            self.assertIn("suggested_next_range", row)
            self.assertIn("trials", row)
            # Importance is the parameter_scout score; nothing scanned is empty.
            self.assertGreater(len(row["trials"]), 0)
        # Rows are sorted by importance descending (None -> bottom).
        importances = [row.get("importance") for row in payload["parameters"]]
        finite = [v for v in importances if v is not None]
        self.assertEqual(finite, sorted(finite, reverse=True))


if __name__ == "__main__":
    unittest.main()
