"""Tests for the audit-cleanup-era behaviour of agentic_swmm/commands/audit.py.

PRD M7:
- writes audit artefacts to 09_audit/ only (not run-dir root)
- on re-audit, renames the existing 09_audit/experiment_note.md to
  experiment_note.<utc-ts>.md.bak before writing the new one (same for
  experiment_provenance.json and comparison.json)
- after a successful audit, regenerates runs/INDEX.md
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


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
                        "source": "Node Inflow Summary",
                    }
                },
                "return_code": 0,
            }
        ),
        encoding="utf-8",
    )


def _run_audit_cli(run_dir: Path, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", "audit", "--run-dir", str(run_dir)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


class AuditCommandAuditDirTests(unittest.TestCase):
    def test_audit_writes_into_09_audit_not_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "case-a"
            run_dir.mkdir(parents=True)
            _seed_runner(run_dir)
            proc = _run_audit_cli(run_dir, cwd=REPO_ROOT)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            audit = run_dir / "09_audit"
            self.assertTrue((audit / "experiment_note.md").exists())
            self.assertTrue((audit / "experiment_provenance.json").exists())
            # Nothing left at root.
            self.assertFalse((run_dir / "experiment_note.md").exists())
            self.assertFalse((run_dir / "experiment_provenance.json").exists())
            # And no 08_audit/ from the old _copy_named_audit_artifacts path.
            self.assertFalse((run_dir / "08_audit").exists())


class ReauditBakRenameTests(unittest.TestCase):
    def test_reaudit_renames_prior_files_to_bak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "case-b"
            run_dir.mkdir(parents=True)
            _seed_runner(run_dir)
            # First audit.
            first = _run_audit_cli(run_dir, cwd=REPO_ROOT)
            self.assertEqual(first.returncode, 0, first.stderr)
            audit = run_dir / "09_audit"
            note_path = audit / "experiment_note.md"
            prov_path = audit / "experiment_provenance.json"
            self.assertTrue(note_path.exists())
            first_note = note_path.read_text(encoding="utf-8")

            # Re-audit (no source files changed). The current file must be
            # backed up before being rewritten.
            second = _run_audit_cli(run_dir, cwd=REPO_ROOT)
            self.assertEqual(second.returncode, 0, second.stderr)

            # Current files still exist.
            self.assertTrue(note_path.exists())
            self.assertTrue(prov_path.exists())

            # At least one .bak sibling exists matching the pattern
            # experiment_note.<utc-ts>.md.bak.
            backups = list(audit.glob("experiment_note.*.md.bak"))
            self.assertGreaterEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), first_note)

            # The PRD also asks for JSON backups.
            self.assertTrue(any(audit.glob("experiment_provenance.*.json.bak")))


class IndexMocTests(unittest.TestCase):
    def test_audit_completion_regenerates_runs_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs_root = tmp_path / "runs"
            run_dir = runs_root / "case-c"
            run_dir.mkdir(parents=True)
            _seed_runner(run_dir)
            env = os.environ.copy()
            # The MOC writer must use the run-dir's parent (runs/) as the
            # MOC root. We point AISWMM_RUNS_ROOT so the test does not
            # depend on cwd-relative resolution.
            env["AISWMM_RUNS_ROOT"] = str(runs_root)
            proc = subprocess.run(
                [sys.executable, "-m", "agentic_swmm.cli", "audit", "--run-dir", str(run_dir)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            index = runs_root / "INDEX.md"
            self.assertTrue(index.exists(), "audit must regenerate runs/INDEX.md")
            text = index.read_text(encoding="utf-8")
            self.assertIn("type: runs-index", text)
            self.assertIn("case-c", text)
            self.assertIn("09_audit/experiment_note", text)


if __name__ == "__main__":
    unittest.main()
