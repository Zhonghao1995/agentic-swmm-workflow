"""Integration tests for scripts/migrate_audit_layout.py.

PRD requires:
  - --dry-run default, --apply performs moves
  - All P1-P5 patterns converge into <run-dir>/09_audit/
  - P4 (GIS-style audit/) is renamed to 09_audit/ without rewriting
    contents; a stub experiment_note.md is synthesized when missing
  - P5 empty 06_audit/ is rmdir'd
  - Idempotent: re-running on a migrated tree is a no-op
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "migrate_audit_layout.py"


def _seed_p1(runs: Path) -> Path:
    case = runs / "real-todcreek-minimal"
    case.mkdir(parents=True)
    (case / "experiment_note.md").write_text("---\ntype: experiment-audit\n---\nP1 note\n", encoding="utf-8")
    (case / "experiment_provenance.json").write_text(
        json.dumps({"schema_version": "1.0"}), encoding="utf-8"
    )
    (case / "comparison.json").write_text(json.dumps({}), encoding="utf-8")
    (case / "05_builder").mkdir()
    return case


def _seed_p2(runs: Path) -> Path:
    case = runs / "benchmarks" / "tecnopolo-prepared"
    case.mkdir(parents=True)
    (case / "experiment_note.md").write_text("---\ntype: experiment-audit\n---\nP2 note\n", encoding="utf-8")
    (case / "experiment_provenance.json").write_text(json.dumps({}), encoding="utf-8")
    return case


def _seed_p3(runs: Path) -> Path:
    case = runs / "external-case-candidates" / "zenodo" / "month-1" / "runner"
    case.mkdir(parents=True)
    (case / "experiment_note.md").write_text("---\ntype: experiment-audit\n---\nP3 note\n", encoding="utf-8")
    (case / "experiment_provenance.json").write_text(json.dumps({}), encoding="utf-8")
    return case


def _seed_p4(runs: Path) -> Path:
    case = runs / "test-gis-entropy"
    audit = case / "audit"
    audit.mkdir(parents=True)
    (audit / "method_summary.md").write_text("# GIS method\nSummary line.\n", encoding="utf-8")
    (audit / "input_checksums.json").write_text(json.dumps({"a.tif": "deadbeef"}), encoding="utf-8")
    (audit / "processing_commands.json").write_text(json.dumps([]), encoding="utf-8")
    (audit / "qgis_entropy_run_manifest.json").write_text(json.dumps({}), encoding="utf-8")
    return case


def _seed_p5(runs: Path) -> Path:
    case = runs / "todcreek-fullpipeline"
    (case / "06_audit").mkdir(parents=True)
    # Add a sentinel file outside 06_audit/ so the empty audit dir
    # survives git's "no empty tracked dir" rule while the tests run.
    (case / "manifest.json").write_text("{}", encoding="utf-8")
    return case


def _seed_already_migrated(runs: Path) -> Path:
    case = runs / "already-migrated"
    audit = case / "09_audit"
    audit.mkdir(parents=True)
    (audit / "experiment_note.md").write_text("---\ntype: experiment-audit\n---\n", encoding="utf-8")
    (audit / "experiment_provenance.json").write_text(json.dumps({"schema_version": "1.1"}), encoding="utf-8")
    return case


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True)


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT), "--runs-root", str(root / "runs"), *args]
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True)


class MigrateAuditLayoutTests(unittest.TestCase):
    def test_dry_run_does_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            p1 = _seed_p1(runs)
            _git_init(root)
            proc = _run(root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((p1 / "experiment_note.md").exists())
            self.assertFalse((p1 / "09_audit").exists())
            self.assertIn("would", proc.stdout.lower())

    def test_apply_migrates_p1_p2_p3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            p1 = _seed_p1(runs)
            p2 = _seed_p2(runs)
            p3 = _seed_p3(runs)
            _git_init(root)
            proc = _run(root, "--apply")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for case in (p1, p2, p3):
                self.assertFalse((case / "experiment_note.md").exists(), f"{case} still has root note")
                self.assertFalse((case / "experiment_provenance.json").exists())
                self.assertTrue((case / "09_audit" / "experiment_note.md").exists())
                self.assertTrue((case / "09_audit" / "experiment_provenance.json").exists())
            # P1 had comparison.json, must follow into 09_audit/.
            self.assertTrue((p1 / "09_audit" / "comparison.json").exists())

    def test_apply_migrates_p4_gis_audit_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            p4 = _seed_p4(runs)
            _git_init(root)
            proc = _run(root, "--apply")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # audit/ renamed to 09_audit/ without rewriting contents.
            self.assertFalse((p4 / "audit").exists())
            self.assertTrue((p4 / "09_audit" / "method_summary.md").exists())
            self.assertTrue((p4 / "09_audit" / "input_checksums.json").exists())
            self.assertTrue((p4 / "09_audit" / "qgis_entropy_run_manifest.json").exists())
            # A stub experiment_note.md is synthesized with the migration marker.
            note = (p4 / "09_audit" / "experiment_note.md").read_text(encoding="utf-8")
            self.assertIn("source: migrated-from-gis-audit", note)
            self.assertTrue((p4 / "09_audit" / "experiment_provenance.json").exists())

    def test_apply_removes_empty_p5_06_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            p5 = _seed_p5(runs)
            _git_init(root)
            proc = _run(root, "--apply")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse((p5 / "06_audit").exists())

    def test_already_migrated_case_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            migrated = _seed_already_migrated(runs)
            _git_init(root)
            proc = _run(root, "--apply")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # Both files still in place.
            self.assertTrue((migrated / "09_audit" / "experiment_note.md").exists())
            self.assertTrue((migrated / "09_audit" / "experiment_provenance.json").exists())

    def test_reapply_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            p1 = _seed_p1(runs)
            _seed_p4(runs)
            _seed_p5(runs)
            _git_init(root)
            first = _run(root, "--apply")
            self.assertEqual(first.returncode, 0, first.stderr)
            subprocess.run(["git", "commit", "-q", "-am", "applied"], cwd=root, check=True)
            second = _run(root, "--apply")
            self.assertEqual(second.returncode, 0, second.stderr)
            # No further work.
            self.assertIn("nothing to migrate", second.stdout.lower())
            # P1 stays migrated.
            self.assertTrue((p1 / "09_audit" / "experiment_note.md").exists())

    def test_only_flag_restricts_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            p1 = _seed_p1(runs)
            p5 = _seed_p5(runs)
            _git_init(root)
            proc = _run(root, "--apply", "--only", "P1")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((p1 / "09_audit" / "experiment_note.md").exists())
            # P5 must not have been touched.
            self.assertTrue((p5 / "06_audit").exists())


if __name__ == "__main__":
    unittest.main()
