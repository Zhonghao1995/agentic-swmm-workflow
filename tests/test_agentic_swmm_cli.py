from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentic_swmm.utils.paths import script_path


REPO_ROOT = Path(__file__).resolve().parents[1]


class AgenticSwmmCliTests(unittest.TestCase):
    def test_script_path_prefers_source_checkout_resource(self) -> None:
        expected = REPO_ROOT / "skills" / "swmm-runner" / "scripts" / "swmm_runner.py"

        self.assertEqual(script_path("skills", "swmm-runner", "scripts", "swmm_runner.py"), expected)

    def test_help_lists_primary_commands(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("doctor", proc.stdout)
        self.assertIn("run", proc.stdout)
        self.assertIn("audit", proc.stdout)
        self.assertIn("plot", proc.stdout)
        self.assertIn("memory", proc.stdout)

    def test_audit_command_writes_artifacts_without_obsidian_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "case-a"
            runner_dir = run_dir / "05_runner"
            runner_dir.mkdir(parents=True)

            rpt = runner_dir / "model.rpt"
            out = runner_dir / "model.out"
            stdout = runner_dir / "stdout.txt"
            stderr = runner_dir / "stderr.txt"
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
            (runner_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "files": {
                            "rpt": str(rpt),
                            "out": str(out),
                            "stdout": str(stdout),
                            "stderr": str(stderr),
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

            proc = subprocess.run(
                [sys.executable, "-m", "agentic_swmm.cli", "audit", "--run-dir", str(run_dir)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(proc.stdout)

            self.assertTrue((run_dir / "experiment_provenance.json").exists())
            self.assertTrue((run_dir / "comparison.json").exists())
            self.assertTrue((run_dir / "experiment_note.md").exists())
            self.assertTrue((run_dir / "command_trace.json").exists())
            self.assertIsNone(payload["obsidian_note"])


if __name__ == "__main__":
    unittest.main()
