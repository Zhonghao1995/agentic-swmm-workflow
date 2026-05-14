"""SHA assertion: a calibration run must never modify the canonical INP.

Issue #54 mandates that every calibration strategy (SCE-UA, DREAM-ZS,
random, lhs, adaptive) keeps the canonical ``.inp`` on disk unchanged.
The agent emits only the three handover artefacts under ``09_audit/``;
applying the patch is gated behind ``aiswmm calibration accept``.

This test runs a small LHS calibration (the cheapest strategy that
exercises the candidate writer end-to-end), hashes the canonical INP
before and after, and asserts equality. As a bonus it asserts the
three artefacts landed under ``09_audit/`` with the required
``evidence_boundary``.

We use the LHS strategy with a tiny iteration count so the test runs
in a few seconds without needing spotpy. The SCE-UA / DREAM-ZS smoke
tests cover their own per-strategy artefact assertions.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INP_FIXTURE = REPO_ROOT / "examples" / "todcreek" / "model_chicago5min.inp"
PATCH_MAP = REPO_ROOT / "examples" / "calibration" / "patch_map.json"
SEARCH_SPACE = REPO_ROOT / "examples" / "calibration" / "search_space.json"
OBSERVED_CSV = REPO_ROOT / "examples" / "calibration" / "observed_flow.csv"
CALIBRATE_PY = REPO_ROOT / "skills" / "swmm-calibration" / "scripts" / "swmm_calibrate.py"


def _has_swmm5() -> bool:
    return shutil.which("swmm5") is not None


@pytest.mark.skipif(not _has_swmm5(), reason="swmm5 binary not available on PATH")
class CanonicalInpUnchangedAfterCalibrationTests(unittest.TestCase):
    """End-to-end: calibration writes 09_audit/ artefacts, leaves INP intact."""

    def test_lhs_strategy_does_not_modify_canonical_inp(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = tmp_path / "runs" / "case-lhs"
            run_dir.mkdir(parents=True)

            # Stage the canonical INP into the run directory so the test
            # checks the SHA of the *run's* on-disk INP rather than the
            # shared examples fixture.
            canonical_inp = run_dir / "model.inp"
            canonical_inp.write_bytes(INP_FIXTURE.read_bytes())
            before_sha = hashlib.sha256(canonical_inp.read_bytes()).hexdigest()

            summary_path = run_dir / "09_audit" / "calibration_summary.json"
            cmd = [
                sys.executable,
                str(CALIBRATE_PY),
                "search",
                "--base-inp", str(canonical_inp),
                "--patch-map", str(PATCH_MAP),
                "--search-space", str(SEARCH_SPACE),
                "--observed", str(OBSERVED_CSV),
                "--run-root", str(run_dir / "trials"),
                "--swmm-node", "O1",
                "--swmm-attr", "Total_inflow",
                "--objective", "nse",
                "--summary-json", str(summary_path),
                "--strategy", "lhs",
                "--iterations", "2",
                "--seed", "7",
                "--candidate-run-dir", str(run_dir),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(
                proc.returncode,
                0,
                msg=(
                    "calibration command failed:\n"
                    f"STDOUT\n{proc.stdout}\n"
                    f"STDERR\n{proc.stderr}"
                ),
            )

            after_sha = hashlib.sha256(canonical_inp.read_bytes()).hexdigest()
            self.assertEqual(
                before_sha,
                after_sha,
                msg=(
                    "Canonical INP SHA changed during calibration — issue #54 "
                    "forbids modifying the on-disk INP before "
                    "`aiswmm calibration accept`."
                ),
            )

            audit_dir = run_dir / "09_audit"
            cand_path = audit_dir / "candidate_calibration.json"
            patch_path = audit_dir / "candidate_inp_patch.json"
            report_path = audit_dir / "calibration_report.md"
            self.assertTrue(cand_path.is_file(), msg=f"missing {cand_path}")
            self.assertTrue(patch_path.is_file(), msg=f"missing {patch_path}")
            self.assertTrue(report_path.is_file(), msg=f"missing {report_path}")
            candidate = json.loads(cand_path.read_text(encoding="utf-8"))
            self.assertEqual(
                candidate["evidence_boundary"],
                "candidate_not_accepted_yet",
            )
            # Make sure the candidate names the LHS strategy that produced it.
            self.assertIn(candidate["strategy"], {"lhs", "random", "adaptive"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
