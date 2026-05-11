from __future__ import annotations

import json
import os
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
        self.assertIn("agent", proc.stdout)
        self.assertIn("chat", proc.stdout)
        self.assertIn("model", proc.stdout)
        self.assertIn("config", proc.stdout)
        self.assertIn("setup", proc.stdout)
        self.assertIn("mcp", proc.stdout)
        self.assertIn("skill", proc.stdout)
        self.assertIn("run", proc.stdout)
        self.assertIn("audit", proc.stdout)
        self.assertIn("plot", proc.stdout)
        self.assertIn("memory", proc.stdout)

    def test_model_config_uses_isolated_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "model",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-test",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("provider: openai", proc.stdout)
            self.assertIn("model: gpt-test", proc.stdout)
            self.assertTrue((Path(tmp) / "config.toml").exists())

    def test_setup_accepts_newer_openai_model_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "setup",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--json",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(proc.stdout)

            self.assertEqual(payload["provider"]["model"], "gpt-5.4")

    def test_openai_chat_mock_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "mocked swmm answer"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "chat",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-test",
                    "summarize",
                    "this",
                    "run",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertEqual(proc.stdout.strip(), "mocked swmm answer")

    def test_cli_without_command_defaults_to_chat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "mocked default chat"
            proc = subprocess.run(
                [sys.executable, "-m", "agentic_swmm.cli"],
                cwd=REPO_ROOT,
                env=env,
                input="/exit\n",
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("Welcome to Agentic SWMM.", proc.stdout)

    def test_agent_dry_run_plans_acceptance_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "agent",
                    "--session-dir",
                    str(Path(tmp) / "agent-session"),
                    "--dry-run",
                    "run acceptance and audit",
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "AISWMM_CONFIG_DIR": tmp},
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("Agentic SWMM executor", proc.stdout)
            self.assertIn("demo_acceptance", proc.stdout)
            self.assertIn("audit_run", proc.stdout)
            report = Path(tmp) / "agent-session" / "final_report.md"
            self.assertTrue(report.exists())

    def test_agent_openai_planner_uses_mock_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_TOOL_CALLS"] = json.dumps(
                [
                    {"name": "doctor", "arguments": {}},
                    {"name": "read_file", "arguments": {"path": "README.md"}},
                ]
            )
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "mock planner final"
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

            self.assertIn("planner: openai", proc.stdout)
            self.assertIn("doctor", proc.stdout)
            report = (session_dir / "final_report.md").read_text(encoding="utf-8")
            self.assertIn("- planner: openai", report)
            self.assertIn("allowed_tools", report)

    def test_agent_openai_planner_rejects_unsupported_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_TOOL_CALLS"] = json.dumps([{"name": "shell", "arguments": {"cmd": "pwd"}}])
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
                    str(Path(tmp) / "agent-session"),
                    "try shell",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("unsupported tool", proc.stderr)

    def test_agent_openai_planner_can_read_skill_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_TOOL_CALLS"] = json.dumps(
                [
                    {"name": "list_skills", "arguments": {}},
                    {"name": "read_skill", "arguments": {"skill_name": "swmm-runner"}},
                ]
            )
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "skill contracts inspected"
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
                    "inspect runner skill",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("list_skills", proc.stdout)
            self.assertIn("read_skill", proc.stdout)
            self.assertIn("skill contracts inspected", proc.stdout)
            report = (session_dir / "final_report.md").read_text(encoding="utf-8")
            self.assertIn("read_skill", report)

    def test_agent_openai_planner_rejects_run_inp_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside_inp = Path(tmp) / "outside.inp"
            outside_inp.write_text("[TITLE]\nOutside\n", encoding="utf-8")
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_TOOL_CALLS"] = json.dumps(
                [{"name": "run_swmm_inp", "arguments": {"inp_path": str(outside_inp)}}]
            )
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
                    str(Path(tmp) / "agent-session"),
                    "run outside inp",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("inp_path must be inside repository", proc.stdout)

    def test_skill_and_mcp_lists_are_available(self) -> None:
        skill_proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "skill", "list"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("swmm-end-to-end", skill_proc.stdout)

        mcp_proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "mcp", "list"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("swmm-runner", mcp_proc.stdout)

    def test_setup_mounts_repo_resources_into_local_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "setup",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-test",
                    "--json",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(proc.stdout)

            self.assertIn(payload["status"], {"ready", "ready_with_warnings"})
            self.assertEqual(payload["resources"]["skills"], 12)
            self.assertEqual(payload["resources"]["mcp_servers"], 8)
            self.assertEqual(payload["resources"]["memory_files"], 10)
            self.assertEqual(payload["resources"]["memory_layers"]["long_term"], 6)
            self.assertEqual(payload["resources"]["memory_layers"]["project_modeling"], 4)
            self.assertTrue((Path(tmp) / "config.toml").exists())
            self.assertTrue((Path(tmp) / "skills.json").exists())
            self.assertTrue((Path(tmp) / "mcp.json").exists())
            self.assertTrue((Path(tmp) / "memory.json").exists())
            self.assertTrue((Path(tmp) / "setup_state.json").exists())

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
