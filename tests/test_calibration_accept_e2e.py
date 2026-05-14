"""End-to-end: calibrate -> candidate written -> accept -> INP patched (#54).

Workflow under test::

    1. Seed a run dir with a canonical INP.
    2. Run ``swmm_calibrate.py search --strategy lhs`` so the writer
       deposits the three candidate-handover artefacts in 09_audit/.
    3. Confirm the canonical INP is **untouched** at this point.
    4. Run ``aiswmm calibration accept <run_dir>``.
    5. Confirm:
       a. The CLI succeeded.
       b. The canonical INP SHA *changed* (the patch landed on disk).
       c. A ``calibration_accept`` row appears in human_decisions with
          ``evidence_ref`` pointing at ``candidate_calibration.json``.
       d. The recorded ``decision_text`` mentions the patch SHA.
"""

from __future__ import annotations

import hashlib
import json
import os
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


def _aiswmm(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


@pytest.mark.skipif(not _has_swmm5(), reason="swmm5 binary not available on PATH")
class CalibrationAcceptE2ETests(unittest.TestCase):
    """A short LHS calibration followed by an expert-only accept."""

    def _seed_run(self, tmp: Path) -> Path:
        run_dir = tmp / "runs" / "case-accept"
        audit = run_dir / "09_audit"
        audit.mkdir(parents=True)
        # Seed a v1.1 provenance file so the accept CLI has somewhere to
        # append the human_decisions record. This mirrors what the audit
        # pipeline emits.
        (audit / "experiment_provenance.json").write_text(
            json.dumps({"schema_version": "1.1", "run_id": "case-accept"}),
            encoding="utf-8",
        )
        return run_dir

    def _calibrate(self, run_dir: Path) -> None:
        canonical_inp = run_dir / "model.inp"
        canonical_inp.write_bytes(INP_FIXTURE.read_bytes())
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
            "--seed", "11",
            "--candidate-run-dir", str(run_dir),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            proc.returncode,
            0,
            msg=(
                "swmm_calibrate failed during e2e setup:\n"
                f"STDOUT\n{proc.stdout}\nSTDERR\n{proc.stderr}"
            ),
        )

    def test_accept_applies_patch_and_records_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = self._seed_run(Path(tmp))
            self._calibrate(run_dir)

            canonical_inp = run_dir / "model.inp"
            sha_before_accept = hashlib.sha256(canonical_inp.read_bytes()).hexdigest()

            # Candidate file was written; canonical INP is still unchanged.
            cand_path = run_dir / "09_audit" / "candidate_calibration.json"
            self.assertTrue(cand_path.is_file(), msg=f"missing {cand_path}")
            cand = json.loads(cand_path.read_text(encoding="utf-8"))
            recorded_sha = cand["candidate_inp_patch_sha256"]

            # Sanity check: the candidate's record of the canonical INP
            # SHA matches what is on disk pre-accept.
            self.assertEqual(
                cand["canonical_inp_sha256_at_candidate_time"],
                sha_before_accept,
            )

            # Run the accept CLI.
            env_user = os.environ.get("USER", "tester")
            proc = _aiswmm("calibration", "accept", str(run_dir))
            self.assertEqual(
                proc.returncode,
                0,
                msg=(
                    "aiswmm calibration accept failed:\n"
                    f"STDOUT\n{proc.stdout}\nSTDERR\n{proc.stderr}"
                ),
            )

            sha_after_accept = hashlib.sha256(canonical_inp.read_bytes()).hexdigest()
            self.assertNotEqual(
                sha_before_accept,
                sha_after_accept,
                msg="canonical INP unchanged after accept — patch never applied",
            )

            prov = json.loads(
                (run_dir / "09_audit" / "experiment_provenance.json").read_text(
                    encoding="utf-8"
                )
            )
            decisions = [
                d
                for d in (prov.get("human_decisions") or [])
                if d.get("action") == "calibration_accept"
            ]
            self.assertEqual(len(decisions), 1)
            decision = decisions[0]
            self.assertEqual(decision["by"], env_user)
            self.assertIn("candidate_calibration.json", str(decision.get("evidence_ref")))
            # The decision_text should reference the recorded patch SHA
            # so an auditor can trace which patch was applied without
            # re-reading the candidate file.
            self.assertIn(recorded_sha, str(decision.get("decision_text")))

    def test_accept_refuses_when_candidate_missing(self) -> None:
        """`aiswmm calibration accept` must refuse if no candidate exists."""

        with TemporaryDirectory() as tmp:
            run_dir = self._seed_run(Path(tmp))
            # Note: we explicitly do NOT call _calibrate, so no
            # candidate_calibration.json exists.
            proc = _aiswmm("calibration", "accept", str(run_dir))
        self.assertNotEqual(proc.returncode, 0)
        haystack = proc.stderr + proc.stdout
        self.assertIn("candidate_calibration.json", haystack)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
