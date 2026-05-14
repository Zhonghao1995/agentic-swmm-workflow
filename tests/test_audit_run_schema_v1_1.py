"""Tests for the schema-1.2 bump + pre-migration validator in audit_run.py.

PRD: ``.claude/prds/PRD_audit.md`` M6, updated by PRD-Z which bumps
the schema 1.1 -> 1.2 to add an optional ``human_decisions`` array.
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
                        "source": "Node Inflow Summary",
                    }
                },
                "return_code": 0,
            }
        ),
        encoding="utf-8",
    )


class SchemaBumpTests(unittest.TestCase):
    def test_audit_writes_schema_1_2_into_09_audit(self) -> None:
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
                    "--no-obsidian",
                ],
                check=True,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            prov = json.loads((run_dir / "09_audit" / "experiment_provenance.json").read_text(encoding="utf-8"))
            # PRD-CASE-ID: schema bumped 1.2 -> 1.3 to add an optional case_id.
            # human_decisions remains an optional array (carried from PRD-Z).
            self.assertEqual(prov["schema_version"], "1.3")
            self.assertEqual(prov["human_decisions"], [])
            self.assertIsNone(prov.get("case_id"))
            self.assertTrue((run_dir / "09_audit" / "experiment_note.md").exists())
            self.assertTrue((run_dir / "09_audit" / "comparison.json").exists())
            # Legacy root-level paths must NOT be written by the bumped script.
            self.assertFalse((run_dir / "experiment_note.md").exists())
            self.assertFalse((run_dir / "experiment_provenance.json").exists())


class PreMigrationValidatorTests(unittest.TestCase):
    def test_rejects_pre_migration_layout_with_clear_error(self) -> None:
        """If experiment_provenance.json sits at the run-dir root, the
        script must refuse to overwrite it and point the user at the
        migration script.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run_dir = repo / "runs" / "legacy-case"
            run_dir.mkdir(parents=True)
            _seed_runner(run_dir)
            # Seed a pre-1.1 root-level audit footprint (P1).
            (run_dir / "experiment_note.md").write_text("legacy note", encoding="utf-8")
            (run_dir / "experiment_provenance.json").write_text(
                json.dumps({"schema_version": "1.0"}), encoding="utf-8"
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
            self.assertNotEqual(proc.returncode, 0)
            err = proc.stderr + proc.stdout
            self.assertIn("migrate_audit_layout.py", err)
            # The legacy file is left untouched.
            self.assertEqual((run_dir / "experiment_note.md").read_text(encoding="utf-8"), "legacy note")

    def test_passes_when_already_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run_dir = repo / "runs" / "case-b"
            run_dir.mkdir(parents=True)
            _seed_runner(run_dir)
            # Already-migrated layout: 09_audit/ exists with a prior 1.1 note.
            audit = run_dir / "09_audit"
            audit.mkdir()
            (audit / "experiment_note.md").write_text("prior", encoding="utf-8")
            (audit / "experiment_provenance.json").write_text(
                json.dumps({"schema_version": "1.1"}), encoding="utf-8"
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
            self.assertTrue((audit / "experiment_note.md").exists())


if __name__ == "__main__":
    unittest.main()
