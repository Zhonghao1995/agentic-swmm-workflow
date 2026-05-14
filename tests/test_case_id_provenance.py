"""Tests for the v1.2 -> v1.3 provenance bump (PRD-CASE-ID).

Two contracts:

1. A run audited with ``--case-id tod-creek`` writes
   ``experiment_provenance.json`` with ``schema_version: "1.3"`` and
   ``case_id: "tod-creek"``.
2. A pre-existing v1.2 provenance file (no ``case_id``) reads back as
   ``case_id: null`` without crashing — back-compat for runs audited
   before this PRD landed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


def _seed_runner(run_dir: Path) -> None:
    runner = run_dir / "05_runner"
    runner.mkdir(parents=True)
    (runner / "model.rpt").write_text(
        """
        ***** Node Inflow Summary *****
        ------------------------------------------------
          O1              OUTFALL       0.001       1.250      2    12:47

        ***** Flow Routing Continuity *****
        Continuity Error (%) ............. 0.00
        """,
        encoding="utf-8",
    )
    (runner / "model.out").write_text("binary-placeholder", encoding="utf-8")
    (runner / "stdout.txt").write_text("", encoding="utf-8")
    (runner / "stderr.txt").write_text("", encoding="utf-8")
    (runner / "manifest.json").write_text(
        json.dumps(
            {
                "files": {
                    "rpt": str(runner / "model.rpt"),
                    "out": str(runner / "model.out"),
                    "stdout": str(runner / "stdout.txt"),
                    "stderr": str(runner / "stderr.txt"),
                },
                "metrics": {
                    "peak": {
                        "node": "O1",
                        "peak": 1.25,
                        "time_hhmm": "12:47",
                        "source": "rpt",
                    },
                    "continuity": {
                        "continuity_error_percent": {"flow_routing": 0.0},
                    },
                },
                "return_code": 0,
                "swmm5": {"version": "5.2.x"},
            }
        ),
        encoding="utf-8",
    )


class V1_3SchemaBumpTests(unittest.TestCase):
    def test_audit_run_records_case_id_with_v1_3_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run_dir = repo / "runs" / "case-a"
            run_dir.mkdir(parents=True)
            _seed_runner(run_dir)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_SCRIPT),
                    "--run-dir",
                    str(run_dir),
                    "--repo-root",
                    str(repo),
                    "--case-id",
                    "tod-creek",
                    "--no-obsidian",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            prov = json.loads(
                (run_dir / "09_audit" / "experiment_provenance.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(prov["schema_version"], "1.3")
        self.assertEqual(prov["case_id"], "tod-creek")

    def test_audit_run_omits_case_id_when_flag_absent(self) -> None:
        """No --case-id flag -> case_id key written as null (back-compat)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run_dir = repo / "runs" / "case-b"
            run_dir.mkdir(parents=True)
            _seed_runner(run_dir)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_SCRIPT),
                    "--run-dir",
                    str(run_dir),
                    "--repo-root",
                    str(repo),
                    "--no-obsidian",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            prov = json.loads(
                (run_dir / "09_audit" / "experiment_provenance.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(prov["schema_version"], "1.3")
        self.assertIsNone(prov.get("case_id"))


class V1_2BackCompatTests(unittest.TestCase):
    """A pre-existing v1.2 provenance (no case_id) must read without crash."""

    def test_re_audit_v1_2_provenance_does_not_crash(self) -> None:
        """Re-running audit_run.py against a run with a pre-existing v1.2
        provenance file must succeed and write a v1.3 file. case_id is
        ``null`` because the v1.2 file did not carry one.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run_dir = repo / "runs" / "legacy"
            run_dir.mkdir(parents=True)
            _seed_runner(run_dir)
            # Seed a pre-existing v1.2 provenance file alongside the
            # required 09_audit layout — same shape the audit script
            # would have produced before this PRD landed.
            audit_dir = run_dir / "09_audit"
            audit_dir.mkdir()
            (audit_dir / "experiment_provenance.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.2",
                        "run_id": "legacy",
                        "human_decisions": [],
                    }
                ),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_SCRIPT),
                    "--run-dir",
                    str(run_dir),
                    "--repo-root",
                    str(repo),
                    "--no-obsidian",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            prov = json.loads(
                (audit_dir / "experiment_provenance.json").read_text(encoding="utf-8")
            )
        self.assertEqual(prov["schema_version"], "1.3")
        # v1.2 file carried no case_id; the bump records it as null.
        self.assertIsNone(prov.get("case_id"))

    def test_re_audit_preserves_case_id(self) -> None:
        """A second audit on the same run keeps the previously-recorded
        case_id even if the new invocation does not pass --case-id.

        This mirrors the human_decisions preservation pattern in the
        existing v1.1 -> v1.2 bump (decision_recorder.py).
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run_dir = repo / "runs" / "case-c"
            run_dir.mkdir(parents=True)
            _seed_runner(run_dir)
            # First audit declares the case.
            subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_SCRIPT),
                    "--run-dir",
                    str(run_dir),
                    "--repo-root",
                    str(repo),
                    "--case-id",
                    "tod-creek",
                    "--no-obsidian",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            # Re-audit without the flag — case_id must persist.
            subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_SCRIPT),
                    "--run-dir",
                    str(run_dir),
                    "--repo-root",
                    str(repo),
                    "--no-obsidian",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            prov = json.loads(
                (run_dir / "09_audit" / "experiment_provenance.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(prov["case_id"], "tod-creek")
        self.assertEqual(prov["schema_version"], "1.3")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
