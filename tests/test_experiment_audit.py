from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


class ExperimentAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.run_dir = self.repo_root / "runs" / "case-a"
        self.runner_dir = self.run_dir / "05_runner"
        self.runner_dir.mkdir(parents=True)

        rpt = self.runner_dir / "model.rpt"
        out = self.runner_dir / "model.out"
        stdout = self.runner_dir / "stdout.txt"
        stderr = self.runner_dir / "stderr.txt"

        rpt.write_text(
            """
            ***** Node Inflow Summary *****
            ------------------------------------------------
              O1              OUTFALL       0.001       1.250      2    12:47

            ***** Flow Routing Continuity *****
            Continuity Error (%) ............. 0.00
            """,
            encoding="utf-8",
        )
        out.write_text("binary-placeholder", encoding="utf-8")
        stdout.write_text("", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")

        (self.runner_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "files": {
                        "rpt": "runs/case-a/05_runner/model.rpt",
                        "out": "runs/case-a/05_runner/model.out",
                        "stdout": "runs/case-a/05_runner/stdout.txt",
                        "stderr": "runs/case-a/05_runner/stderr.txt",
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

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_audit_cli_writes_provenance_comparison_and_note(self) -> None:
        subprocess.run(
            [
                sys.executable,
                str(AUDIT_SCRIPT),
                "--run-dir",
                str(self.run_dir),
                "--repo-root",
                str(self.repo_root),
                "--no-obsidian",
            ],
            check=True,
            cwd=REPO_ROOT,
        )

        audit_dir = self.run_dir / "09_audit"
        provenance_path = audit_dir / "experiment_provenance.json"
        comparison_path = audit_dir / "comparison.json"
        note_path = audit_dir / "experiment_note.md"
        diagnostics_path = audit_dir / "model_diagnostics.json"

        self.assertTrue(provenance_path.exists(), "audit must write into 09_audit/ (schema 1.1)")
        self.assertTrue(comparison_path.exists())
        self.assertTrue(note_path.exists())
        self.assertTrue(diagnostics_path.exists())
        # The script must no longer write root-level audit artefacts.
        self.assertFalse((self.run_dir / "experiment_note.md").exists())
        self.assertFalse((self.run_dir / "experiment_provenance.json").exists())

        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        peak = provenance["metrics"]["peak_flow"]
        self.assertEqual(provenance["schema_version"], "1.1")
        self.assertEqual(provenance["status"], "pass")
        self.assertEqual(diagnostics["generated_by"], "swmm-experiment-audit")
        self.assertEqual(peak["source_section"], "Node Inflow Summary")
        self.assertEqual(peak["source_validation"]["matches_report"], True)
        self.assertIn("model_diagnostics", provenance["artifacts"])
        self.assertIn("primary machine-readable record", note_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
