from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.cli import _route_default_to_agent
from agentic_swmm.commands.agent import _find_repo_inp
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
        self.assertNotIn("chat", proc.stdout)
        self.assertIn("model", proc.stdout)
        self.assertIn("config", proc.stdout)
        self.assertIn("capabilities", proc.stdout)
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

    def test_legacy_chat_command_routes_to_openai_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "mocked agent answer"
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

            self.assertIn("Agentic SWMM executor", proc.stdout)
            self.assertIn("agent> Planner: openai", proc.stdout)
            self.assertIn("mocked agent answer", proc.stdout)

    def test_cli_without_command_defaults_to_openai_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "mocked default agent"
            proc = subprocess.run(
                [sys.executable, "-m", "agentic_swmm.cli", "--model", "gpt-test"],
                cwd=REPO_ROOT,
                env=env,
                input="inspect project\n/exit\n",
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("Agentic SWMM interactive agent", proc.stdout)
            self.assertIn("Agentic SWMM executor", proc.stdout)
            self.assertIn("agent> Planner: openai", proc.stdout)
            self.assertIn("agent> Goal: inspect project", proc.stdout)
            self.assertIn("mocked default agent", proc.stdout)

    def test_natural_language_goal_defaults_to_openai_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "mocked natural language agent"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "inspect",
                    "the",
                    "project",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("Agentic SWMM executor", proc.stdout)
            self.assertIn("agent> Goal: inspect the project", proc.stdout)
            self.assertIn("mocked natural language agent", proc.stdout)

    def test_default_router_preserves_explicit_low_level_run(self) -> None:
        self.assertEqual(_route_default_to_agent([]), ["agent", "--planner", "openai", "--interactive"])
        self.assertEqual(_route_default_to_agent(["chat"]), ["agent", "--planner", "openai", "--interactive"])
        self.assertEqual(
            _route_default_to_agent(["--model", "gpt-test"]),
            ["agent", "--planner", "openai", "--interactive", "--model", "gpt-test"],
        )
        self.assertEqual(_route_default_to_agent(["run", "--inp", "model.inp"]), ["run", "--inp", "model.inp"])
        self.assertEqual(_route_default_to_agent(["capabilities"]), ["capabilities"])
        self.assertEqual(
            _route_default_to_agent(["--verbose", "--model", "gpt-test"]),
            ["agent", "--planner", "openai", "--interactive", "--verbose", "--model", "gpt-test"],
        )
        self.assertEqual(
            _route_default_to_agent(["run", "tecnopolo_r1_199401.inp"]),
            ["agent", "--planner", "openai", "run", "tecnopolo_r1_199401.inp"],
        )

    def test_agent_resolves_bare_inp_names_from_examples(self) -> None:
        self.assertEqual(
            _find_repo_inp("tecnopolo_r1_199401.inp"),
            REPO_ROOT / "examples" / "tecnopolo" / "tecnopolo_r1_199401.inp",
        )

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

            self.assertIn("agent> Planner: openai", proc.stdout)
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

    def test_agent_openai_planner_uses_workspace_and_runtime_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_TOOL_CALLS"] = json.dumps(
                [
                    {"name": "capabilities", "arguments": {}},
                    {"name": "list_dir", "arguments": {"path": "agentic_swmm"}},
                    {"name": "search_files", "arguments": {"query": "Agentic SWMM", "glob": "README.md"}},
                    {"name": "list_mcp_servers", "arguments": {}},
                ]
            )
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "workspace tools inspected"
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
                    "inspect runtime capabilities",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("capabilities", proc.stdout)
            self.assertIn("list_dir", proc.stdout)
            self.assertIn("search_files", proc.stdout)
            self.assertIn("list_mcp_servers", proc.stdout)
            self.assertIn("workspace tools inspected", proc.stdout)
            report = (session_dir / "final_report.md").read_text(encoding="utf-8")
            self.assertIn("allowed_tools", report)
            self.assertIn("web_search", report)
            self.assertIn("call_mcp_tool", report)

    def test_agent_selects_workflow_mode_before_swmm_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_TOOL_CALLS"] = json.dumps(
                [
                    {"name": "select_workflow_mode", "arguments": {"goal": "run external inp", "inp_path": "D:\\models\\case.inp"}},
                ]
            )
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "workflow selected"
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
                    "run external inp",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("select_workflow_mode", proc.stdout)
            report = (session_dir / "final_report.md").read_text(encoding="utf-8")
            self.assertIn("select_workflow_mode", report)
            self.assertIn("workflow selected", report)

    def test_select_workflow_mode_reports_missing_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_TOOL_CALLS"] = json.dumps(
                [
                    {"name": "select_workflow_mode", "arguments": {"goal": "run a SWMM model"}},
                ]
            )
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "need user input"
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
                    "run a SWMM model",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("mode=needs_user_inputs", proc.stdout)
            trace = (session_dir / "agent_trace.jsonl").read_text(encoding="utf-8")
            self.assertIn("SWMM INP path", trace)

    def test_auto_workflow_router_stops_missing_swmm_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_FORCE_AUTO_WORKFLOW_ROUTER"] = "1"
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "should not be used"
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
                    "run a SWMM model",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("select_workflow_mode", proc.stdout)
            self.assertIn("Please provide a SWMM INP path", proc.stdout)

    def test_capabilities_command_lists_new_agent_tools(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "capabilities", "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["filesystem"]["arbitrary_shell"], False)
        self.assertTrue(payload["web"]["enabled"])
        self.assertTrue(payload["mcp"]["enabled"])
        self.assertIn("web_search", payload["tools"])
        self.assertIn("call_mcp_tool", payload["tools"])
        self.assertIn("select_workflow_mode", payload["tools"])
        self.assertIn("apply_patch", payload["tools"])
        self.assertIn("run_tests", payload["tools"])
        self.assertIn("run_allowed_command", payload["tools"])

    def test_agent_blocks_disallowed_shell_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_TOOL_CALLS"] = json.dumps(
                [{"name": "run_allowed_command", "arguments": {"command": ["cmd", "/c", "dir"]}}]
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
                    str(session_dir),
                    "try shell",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("command is not allowlisted", proc.stdout)

    def test_agent_can_run_scoped_pytest_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_TOOL_CALLS"] = json.dumps(
                [{"name": "run_tests", "arguments": {"paths": ["tests/test_swmm_modeling_memory.py"], "timeout_seconds": 60}}]
            )
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "tests checked"
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
                    "run focused tests",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("run_tests", proc.stdout)
            self.assertIn("OK:", proc.stdout)

    def test_openai_agent_writes_session_state_and_context_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "state checked"
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
                    "inspect project state",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("state checked", proc.stdout)
            state = json.loads((session_dir / "session_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["planner"], "openai")
            self.assertIn("retry_policy", state)
            self.assertTrue((session_dir / "context_summary.md").exists())

    def test_mcp_tool_list_returns_mapped_schemas(self) -> None:
        registry = AgentToolRegistry()
        fake_tools = [
            {
                "name": "validate_network",
                "description": "Validate a network.",
                "inputSchema": {"type": "object", "properties": {"network_json": {"type": "string"}}, "required": ["network_json"]},
            }
        ]
        with tempfile.TemporaryDirectory() as tmp, patch("agentic_swmm.agent.mcp_client.list_tools", return_value=fake_tools):
            result = registry.execute(ToolCall("list_mcp_tools", {"server": "swmm-network"}), Path(tmp))

        self.assertTrue(result["ok"])
        self.assertEqual(result["mapped_tools"][0]["planner_tool"], "call_mcp_tool")
        self.assertEqual(result["mapped_tools"][0]["arguments"]["server"], "swmm-network")
        self.assertIn("network_json", result["mapped_tools"][0]["arguments"]["arguments_schema"]["properties"])

    def test_mcp_call_failure_reports_recovery_and_fallback(self) -> None:
        registry = AgentToolRegistry()
        with tempfile.TemporaryDirectory() as tmp, patch("agentic_swmm.agent.mcp_client.call_tool", side_effect=RuntimeError("bad args")):
            result = registry.execute(ToolCall("call_mcp_tool", {"server": "swmm-runner", "tool": "run", "arguments": {}}), Path(tmp))

        self.assertFalse(result["ok"])
        self.assertIn("recovery", result)
        self.assertIn("run_swmm_inp", result["fallback_tools"])

    def test_workflow_router_suggests_nodes_from_inp(self) -> None:
        registry = AgentToolRegistry()
        inp = REPO_ROOT / "examples" / "tecnopolo" / "tecnopolo_r1_199401.inp"
        with tempfile.TemporaryDirectory() as tmp:
            result = registry.execute(ToolCall("select_workflow_mode", {"goal": "run inp", "inp_path": str(inp)}), Path(tmp))

        payload = result["results"]
        self.assertTrue(payload["node_suggestions"])
        self.assertEqual(payload["node_suggestions"][0], "OU2")

    def test_agent_openai_planner_reports_missing_external_inp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside_inp = Path(tmp) / "outside.inp"
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
            self.assertIn("external INP file not found", proc.stdout)

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

    def test_run_imports_external_inp_before_execution_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            external_dir = tmp_path / "external models"
            external_dir.mkdir()
            external_inp = external_dir / "user case.inp"
            external_inp.write_text("[TITLE]\nExternal user model\n", encoding="utf-8")

            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            if os.name == "nt":
                swmm5 = fake_bin / "swmm5.cmd"
                swmm5.write_text(
                    "\n".join(
                        [
                            "@echo off",
                            "if \"%1\"==\"--version\" (echo EPA SWMM 5.2.4 & exit /b 0)",
                            "(",
                            "echo ***** Node Inflow Summary *****",
                            "echo ------------------------------------------------",
                            "echo   O1              OUTFALL       0.001       1.250      2    12:47",
                            "echo.",
                            "echo ***** Flow Routing Continuity *****",
                            "echo Continuity Error (%%) ............. 0.00",
                            ") > \"%2\"",
                            "echo binary-placeholder > \"%3\"",
                            "exit /b 0",
                        ]
                    ),
                    encoding="utf-8",
                )
            else:
                swmm5 = fake_bin / "swmm5"
                swmm5.write_text(
                    "\n".join(
                        [
                            "#!/usr/bin/env sh",
                            "if [ \"$1\" = \"--version\" ]; then echo 'EPA SWMM 5.2.4'; exit 0; fi",
                            "{",
                            "echo '***** Node Inflow Summary *****'",
                            "echo '------------------------------------------------'",
                            "echo '  O1              OUTFALL       0.001       1.250      2    12:47'",
                            "echo",
                            "echo '***** Flow Routing Continuity *****'",
                            "echo 'Continuity Error (%) ............. 0.00'",
                            "} > \"$2\"",
                            "echo 'binary-placeholder' > \"$3\"",
                            "exit 0",
                        ]
                    ),
                    encoding="utf-8",
                )
                swmm5.chmod(0o755)

            run_dir = tmp_path / "runs" / "external-case"
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "run",
                    "--inp",
                    str(external_inp),
                    "--run-dir",
                    str(run_dir),
                    "--node",
                    "O1",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            imported_inp = run_dir / "00_inputs" / "model.inp"
            self.assertTrue(imported_inp.exists())
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["pipeline"], "external_inp_import")
            self.assertEqual(manifest["inputs"]["source_inp"]["path"], str(external_inp.resolve()))
            self.assertEqual(manifest["inputs"]["source_inp"]["sha256"], manifest["inputs"]["run_inp"]["sha256"])
            self.assertEqual(manifest["inputs"]["run_inp"]["path"], str(imported_inp.resolve()))

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "audit",
                    "--run-dir",
                    str(run_dir),
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            provenance = json.loads((run_dir / "experiment_provenance.json").read_text(encoding="utf-8"))
            note = (run_dir / "experiment_note.md").read_text(encoding="utf-8")
            self.assertEqual(provenance["workflow_mode"], "external_inp_import")
            self.assertIn("## Input Provenance", note)
            self.assertIn("External INP import boundary", note)
            self.assertIn("does not claim the external model is calibrated or validated", note)


if __name__ == "__main__":
    unittest.main()
