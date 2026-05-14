"""Wiring test: live session writes the new ``final_report.md``.

PRD_runtime Done Criteria
``test_session_writes_new_report_template``:

- Drive a fixture session to completion via the public executor
  entrypoint and read the resulting ``final_report.md`` from disk.
- Must contain ``## What I did``.
- Must contain ``## What you got``.
- Must NOT contain the inline ``allowed_tools`` comma list (a footer
  reference to ``agent_trace.jsonl`` is fine).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class SessionReportTemplateWiringTests(unittest.TestCase):
    def test_dry_run_session_writes_new_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = {
                "PATH": str(Path("/usr/bin")) + ":" + str(Path("/bin")),
                "AISWMM_CONFIG_DIR": str(Path(tmp) / "cfg"),
                "AISWMM_OPENAI_MOCK_RESPONSE": "ok",
                # Ensure no real OpenAI call; planner stays deterministic.
            }
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "agent",
                    "--planner",
                    "openai",
                    "--model",
                    "gpt-test",
                    "--session-dir",
                    str(session_dir),
                    "--dry-run",
                    "inspect the project",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report_path = session_dir / "final_report.md"
            self.assertTrue(report_path.exists(), "final_report.md must exist")
            text = report_path.read_text(encoding="utf-8")

        self.assertIn("## What I did", text)
        self.assertIn("## What you got", text)
        # Footer reference is the only place the available-tool count
        # is now allowed to surface.
        self.assertIn("agent_trace.jsonl", text)

        # Build the comma list the legacy template used and ensure it
        # is NOT present.
        from agentic_swmm.agent.tool_registry import AgentToolRegistry

        legacy_dump = ", ".join(sorted(AgentToolRegistry().names))
        self.assertNotIn(
            legacy_dump,
            text,
            "Legacy inline allowed_tools comma list must be dropped from the report",
        )


if __name__ == "__main__":
    unittest.main()
