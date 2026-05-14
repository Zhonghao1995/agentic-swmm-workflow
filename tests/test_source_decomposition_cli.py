"""End-to-end test for ``aiswmm uncertainty source <run_dir>`` (#55).

Per the issue acceptance criteria:

- complete run dir → exit 0
- partial run dir (some methods absent) → exit 0 + a clear warning on
  stderr listing which methods are absent
- no uncertainty outputs at all → exit 1

The CLI is the audit/UX surface for the pure ``decompose`` function;
the python-level tests pin the markdown and JSON contracts, this file
pins the shell-level contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]


def _aiswmm(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def _seed_complete(run_dir: Path) -> None:
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "sensitivity_indices.json").write_text(
        json.dumps(
            {
                "method": "sobol",
                "parameters": ["p1", "p2"],
                "sample_budget": 12,
                "indices": {
                    "p1": {"S_i": 0.5, "S_i_conf": 0.05, "S_T_i": 0.7, "S_T_i_conf": 0.05},
                    "p2": {"S_i": 0.2, "S_i_conf": 0.05, "S_T_i": 0.3, "S_T_i_conf": 0.05},
                },
            }
        ),
        encoding="utf-8",
    )
    (audit / "rainfall_ensemble_summary.json").write_text(
        json.dumps(
            {
                "method": "perturbation",
                "n_realisations": 50,
                "rainfall_ensemble_stats": {
                    "peak_intensity_mm_per_hr": {"p05": 1, "p50": 2, "p95": 3},
                    "total_volume_mm": {"p05": 1, "p50": 2, "p95": 3},
                },
            }
        ),
        encoding="utf-8",
    )
    (audit / "posterior_samples.csv").write_text("chain,iteration,likelihood,p1\n1,0,-0.4,30.0\n", encoding="utf-8")
    (audit / "candidate_calibration.json").write_text(
        json.dumps({"strategy": "sce-ua", "primary_objective": "kge", "primary_value": 0.7}),
        encoding="utf-8",
    )
    (audit / "uncertainty_summary.json").write_text(
        json.dumps({"mode": "uncertainty", "samples": 100, "node": "O1"}),
        encoding="utf-8",
    )


def _seed_partial(run_dir: Path) -> None:
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)
    # Only a Sobol' file — everything else absent.
    (audit / "sensitivity_indices.json").write_text(
        json.dumps(
            {
                "method": "sobol",
                "parameters": ["p1"],
                "sample_budget": 6,
                "indices": {
                    "p1": {"S_i": 0.4, "S_i_conf": 0.05, "S_T_i": 0.6, "S_T_i_conf": 0.05},
                },
            }
        ),
        encoding="utf-8",
    )


class UncertaintySourceCliTests(unittest.TestCase):
    def test_complete_run_exits_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "case-complete"
            _seed_complete(run_dir)
            result = _aiswmm("uncertainty", "source", str(run_dir))
            self.assertEqual(
                result.returncode,
                0,
                msg=f"expected exit 0; stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            md = run_dir / "09_audit" / "uncertainty_source_summary.md"
            payload = run_dir / "09_audit" / "uncertainty_source_decomposition.json"
            self.assertTrue(md.is_file())
            self.assertTrue(payload.is_file())
            # stdout should be a JSON record with ok=True + the two output paths
            stdout_payload = json.loads(result.stdout)
            self.assertTrue(stdout_payload.get("ok"))
            self.assertIn("markdown_path", stdout_payload)
            self.assertIn("json_path", stdout_payload)

    def test_partial_run_exits_zero_with_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "case-partial"
            _seed_partial(run_dir)
            result = _aiswmm("uncertainty", "source", str(run_dir))
            self.assertEqual(
                result.returncode,
                0,
                msg=f"expected exit 0 with warning; stderr={result.stderr!r}",
            )
            # stderr must list at least one absent method so an auditor
            # reading the console knows the report is incomplete.
            self.assertIn("warning", result.stderr.lower())
            self.assertIn("Morris", result.stderr)
            self.assertIn("DREAM-ZS", result.stderr)

    def test_empty_run_exits_one(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "case-empty"
            (run_dir / "09_audit").mkdir(parents=True)
            result = _aiswmm("uncertainty", "source", str(run_dir))
            self.assertEqual(
                result.returncode,
                1,
                msg=f"expected exit 1; stderr={result.stderr!r}",
            )

    def test_missing_run_dir_exits_one(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-such-run"
            result = _aiswmm("uncertainty", "source", str(missing))
            self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
