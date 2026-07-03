from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.cli import (
    _reject_unknown_verb,
    _route_default_to_agent,
    build_parser,
)
from agentic_swmm.commands.agent import _find_repo_inp
from agentic_swmm.agent.intent_classifier import load_intent_map
from agentic_swmm.agent.planner import _select_relevant_mcp_servers, _select_relevant_skills
from agentic_swmm.agent.prompts import openai_planner_prompt
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
            env["AISWMM_MEMORY_DIR"] = tmp
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
            env["AISWMM_MEMORY_DIR"] = tmp
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
            env["AISWMM_MEMORY_DIR"] = tmp
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

            self.assertIn("aiswmm> Session:", proc.stdout)
            self.assertIn("aiswmm> Session: openai", proc.stdout)
            self.assertIn("mocked agent answer", proc.stdout)

    def test_cli_without_command_defaults_to_openai_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_MEMORY_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "mocked default agent"
            proc = subprocess.run(
                # ``--provider openai`` (the shipped default) makes the
                # OpenAI mock-response env var drive the turn deterministically.
                [sys.executable, "-m", "agentic_swmm.cli", "--provider", "openai", "--model", "gpt-test"],
                cwd=REPO_ROOT,
                env=env,
                input="inspect project\n/exit\n",
                capture_output=True,
                text=True,
                check=True,
            )

            # Runtime UX PRD trimmed the startup banner to a single line —
            # match the new "aiswmm interactive (...)" header.
            self.assertIn("aiswmm interactive", proc.stdout)
            self.assertIn("aiswmm> Session:", proc.stdout)
            self.assertIn("aiswmm> Session: openai", proc.stdout)
            self.assertIn("aiswmm> Goal: inspect project", proc.stdout)
            self.assertIn("mocked default agent", proc.stdout)

    def test_interactive_new_session_command_switches_context_without_nested_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_MEMORY_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "mocked default agent"
            session_base = Path(tmp) / "interactive"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "agent",
                    "--planner",
                    "openai",
                    "--provider",
                    "openai",
                    "--interactive",
                    "--model",
                    "gpt-test",
                    "--session-dir",
                    str(session_base),
                ],
                cwd=REPO_ROOT,
                env=env,
                input="/new-session\ninspect project\n/exit\n",
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("New session:", proc.stdout)
            self.assertIn("Date folder:", proc.stdout)
            self.assertIn("aiswmm> Goal: inspect project", proc.stdout)
            date_dirs = [path for path in session_base.iterdir() if path.is_dir()]
            self.assertEqual(len(date_dirs), 1)
            output_dirs = [path for path in date_dirs[0].iterdir() if path.is_dir()]
            self.assertEqual(len(output_dirs), 1)
            self.assertIn("_chat", output_dirs[0].name)
            self.assertTrue((date_dirs[0] / "_sessions.jsonl").exists())

    def test_natural_language_goal_defaults_to_openai_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_MEMORY_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "mocked natural language agent"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    # ``openai`` is the default provider; pinning it makes
                    # the OpenAI mock-response env var drive the turn.
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-test",
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

            self.assertIn("aiswmm> Session:", proc.stdout)
            self.assertIn("aiswmm> Goal: inspect the project", proc.stdout)
            self.assertIn("mocked natural language agent", proc.stdout)

    def test_default_router_preserves_explicit_low_level_run(self) -> None:
        # Provider-neutralization: the default router dispatches the
        # provider-neutral ``--planner llm`` token (the backend is
        # resolved from ``provider.default``, openai by default).
        # Pin ``OPENAI_API_KEY`` so the interactive preflight treats a
        # provider as configured and does not downgrade to ``rule``;
        # the no-provider fallback is covered by
        # tests/test_cli_provider_preflight.py.
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            self.assertEqual(_route_default_to_agent([]), ["agent", "--planner", "llm", "--interactive"])
            self.assertEqual(_route_default_to_agent(["chat"]), ["agent", "--planner", "llm", "--interactive"])
            self.assertEqual(
                _route_default_to_agent(["--model", "gpt-test"]),
                ["agent", "--planner", "llm", "--interactive", "--model", "gpt-test"],
            )
            self.assertEqual(_route_default_to_agent(["run", "--inp", "model.inp"]), ["run", "--inp", "model.inp"])
            self.assertEqual(_route_default_to_agent(["capabilities"]), ["capabilities"])
            self.assertEqual(
                _route_default_to_agent(["--verbose", "--model", "gpt-test"]),
                ["agent", "--planner", "llm", "--interactive", "--verbose", "--model", "gpt-test"],
            )
            self.assertEqual(
                _route_default_to_agent(["run", "tecnopolo_r1_199401.inp"]),
                ["agent", "--planner", "llm", "run", "tecnopolo_r1_199401.inp"],
            )

    def test_default_router_preserves_run_help_flags(self) -> None:
        # Regression: ``aiswmm run --help`` must reach the run subparser,
        # not be hijacked to ``aiswmm agent run --help``. The dispatcher
        # used to detect missing ``--inp`` and route to the agent so the
        # natural-language planner could prompt for missing inputs, but
        # ``--help`` is the user asking for the run command's usage —
        # never a goal description.
        self.assertEqual(
            _route_default_to_agent(["run", "--help"]),
            ["run", "--help"],
        )
        self.assertEqual(
            _route_default_to_agent(["run", "-h"]),
            ["run", "-h"],
        )

    def test_unknown_single_token_verb_is_rejected_with_exit_2(self) -> None:
        # Onboarding hole: ``aiswmm bogus`` historically routed to the
        # LLM planner, so a first-time user without an API key saw
        # ``OPENAI_API_KEY is not set`` and concluded the tool requires
        # a key. Reject single-token unknown verbs with argparse's
        # standard exit code 2.
        self.assertEqual(_reject_unknown_verb(["bogus"]), 2)

    def test_unknown_verb_typo_suggests_close_match(self) -> None:
        # ``aiswmm runn`` is a typo of ``run``; the rejection should
        # include a difflib-based "Did you mean" hint.
        import contextlib
        import io

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = _reject_unknown_verb(["runn"])
        self.assertEqual(code, 2)
        message = stderr.getvalue()
        self.assertIn("unknown command 'runn'", message)
        self.assertIn("Did you mean 'run'", message)

    def test_non_ascii_single_token_goal_is_not_rejected(self) -> None:
        """A Chinese goal often contains no spaces; it must reach the LLM
        router instead of the typo rejector (every verb is ASCII)."""
        from agentic_swmm.cli import _reject_unknown_verb, _route_default_to_agent

        goal = "看看项目里有什么例子"
        self.assertIsNone(_reject_unknown_verb([goal]))
        routed = _route_default_to_agent([goal])
        self.assertEqual(routed[:3], ["agent", "--planner", "llm"])
        self.assertIn(goal, routed)

    def test_unknown_verb_hint_points_at_the_llm_chat_surface(self) -> None:
        """The escape-hatch hint must name a command that actually routes
        to the LLM planner (``chat``), not the rule-planner default."""
        import io
        from contextlib import redirect_stderr

        from agentic_swmm.cli import _reject_unknown_verb

        buf = io.StringIO()
        with redirect_stderr(buf):
            code = _reject_unknown_verb(["bogusverb"])
        self.assertEqual(code, 2)
        self.assertIn('aiswmm chat "bogusverb"', buf.getvalue())

    def test_turn_preamble_is_two_lines_in_process(self) -> None:
        """The per-turn preamble is Goal + one Session line (provider,
        model, run dir) — the old four-line block ('aiswmm executor' /
        Planner / Evidence folder) stays gone. In-process so the lines
        count toward coverage and the shape is pinned at unit level."""
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        from agentic_swmm.cli import main as cli_main

        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "AISWMM_CONFIG_DIR": tmp,
                "AISWMM_MEMORY_DIR": tmp,
                "AISWMM_OPENAI_MOCK_RESPONSE": "mocked in-process answer",
            }
            buf = io.StringIO()
            with mock.patch.dict(os.environ, env), redirect_stdout(buf):
                rc = cli_main(
                    [
                        "chat",
                        "--provider",
                        "openai",
                        "--model",
                        "gpt-test",
                        "inspect",
                        "the",
                        "project",
                    ]
                )
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("aiswmm> Goal: inspect the project", out)
        self.assertIn("aiswmm> Session: openai (gpt-test) →", out)
        self.assertNotIn("aiswmm executor", out)
        self.assertNotIn("Evidence folder", out)
        self.assertIn("mocked in-process answer", out)


    def test_known_verbs_and_flags_are_not_rejected(self) -> None:
        # The rejection must not fire for known verbs, flags, the
        # legacy ``chat`` alias, multi-token natural-language goals,
        # or a quoted goal containing whitespace.
        self.assertIsNone(_reject_unknown_verb([]))
        self.assertIsNone(_reject_unknown_verb(["run"]))
        self.assertIsNone(_reject_unknown_verb(["doctor"]))
        self.assertIsNone(_reject_unknown_verb(["chat"]))
        self.assertIsNone(_reject_unknown_verb(["--help"]))
        self.assertIsNone(_reject_unknown_verb(["--model", "gpt-test"]))
        # Multi-token goal — natural-language path still works.
        self.assertIsNone(_reject_unknown_verb(["inspect", "the", "project"]))
        # Single quoted goal that contains whitespace — also NL.
        self.assertIsNone(_reject_unknown_verb(["inspect the project"]))

    def test_unknown_verb_rejection_via_subprocess_exits_2(self) -> None:
        # End-to-end: invoking the CLI with an unknown single-token verb
        # must exit with code 2 and write the rejection to stderr. We
        # specifically check that no LLM call is attempted (``aiswmm
        # bogus`` should never reach the agent planner).
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "bogus"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown command 'bogus'", proc.stderr)
        self.assertNotIn("OPENAI_API_KEY", proc.stderr)

    def test_agent_resolves_bare_inp_names_from_examples(self) -> None:
        self.assertEqual(
            _find_repo_inp("tecnopolo_r1_199401.inp"),
            REPO_ROOT / "examples" / "tecnopolo" / "tecnopolo_r1_199401.inp",
        )

    def test_agent_default_step_budget_covers_run_audit_plot(self) -> None:
        # The default --max-steps was bumped from 16 -> 40 in v0.7.1 to
        # give the LLM-driven dispatch loop headroom after the ~15-step
        # introspection (list_skills / read_skill / list_mcp_tools /
        # select_skill) gpt-5.5 typically does before the first real op.
        # The precise default + override behaviour is pinned by
        # ``tests/test_max_steps_default.py``; this assertion keeps the
        # legacy "run + audit + plot is budgeted by default" sanity
        # check in the broader CLI suite.
        parser = build_parser()
        args = parser.parse_args(["agent", "run", "examples/tecnopolo"])

        self.assertEqual(args.max_steps, 40)

    def test_openai_planner_prompt_loads_startup_identity_memory(self) -> None:
        prompt = openai_planner_prompt()

        self.assertIn("Startup memory: identification_memory.md", prompt)
        # PR #74 rewrote the agent's startup memory in first-person warm
        # identity ("I am **aiswmm**, ...") instead of the older
        # second-person framing ("You are **aiswmm**").
        self.assertIn("I am **aiswmm**", prompt)

    def test_intent_map_is_external_config(self) -> None:
        payload = load_intent_map()

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertIn("swmm-end-to-end", payload["always_load_skills"])
        uncertainty = next(intent for intent in payload["intents"] if intent["id"] == "uncertainty")
        self.assertIn("required_inputs", uncertainty)
        self.assertIn("preferred_tools", uncertainty)
        self.assertIn("stop_conditions", uncertainty)
        self.assertIn("next_user_prompt", uncertainty)
        self.assertIn("swmm-runner", payload["mcp_enabled_skills"])

    def test_relevant_skill_selection_is_dynamic(self) -> None:
        self.assertEqual(
            _select_relevant_skills("run examples/tecnopolo/model.inp and audit it")[:3],
            ["swmm-end-to-end", "swmm-runner", "swmm-experiment-audit"],
        )
        self.assertIn("swmm-calibration", _select_relevant_skills("run sensitivity calibration with observed flow and NSE"))
        self.assertIn("swmm-uncertainty", _select_relevant_skills("propagate fuzzy alpha-cut uncertainty"))
        self.assertIn("swmm-gis", _select_relevant_skills("build from GeoPackage GIS subcatchment data"))
        self.assertIn("swmm-climate", _select_relevant_skills("format rainfall raingage timeseries"))
        self.assertIn("swmm-network", _select_relevant_skills("check junction conduit outfall network"))
        self.assertIn("swmm-builder", _select_relevant_skills("build INP from network_json and subcatchments_csv"))
        self.assertIn("swmm-modeling-memory", _select_relevant_skills("summarize modeling memory lessons"))

    def test_relevant_mcp_selection_follows_mcp_enabled_skills(self) -> None:
        # Issue #124 Part D: ``mcp_enabled_skills`` now covers all 11 shipped
        # MCP servers, so ``swmm-experiment-audit`` and ``swmm-uncertainty``
        # pass through. ``swmm-end-to-end`` is still skill-only (no MCP).
        self.assertEqual(
            _select_relevant_mcp_servers(["swmm-end-to-end", "swmm-runner", "swmm-plot", "swmm-experiment-audit"]),
            ["swmm-runner", "swmm-plot", "swmm-experiment-audit"],
        )
        self.assertEqual(
            _select_relevant_mcp_servers(["swmm-calibration", "swmm-uncertainty", "swmm-gis"]),
            ["swmm-calibration", "swmm-uncertainty", "swmm-gis"],
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

            self.assertIn("aiswmm> Session:", proc.stdout)
            self.assertIn("demo_acceptance", proc.stdout)
            self.assertIn("audit_run", proc.stdout)
            report = Path(tmp) / "agent-session" / "final_report.md"
            self.assertTrue(report.exists())

    def test_agent_openai_planner_uses_mock_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_MEMORY_DIR"] = tmp
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
                    "--provider",
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

            self.assertIn("aiswmm> Session: openai", proc.stdout)
            self.assertIn("doctor", proc.stdout)
            report = (session_dir / "final_report.md").read_text(encoding="utf-8")
            self.assertIn("- planner: openai", report)
            # Runtime UX PRD: inline allowed_tools list dropped; report
            # now references agent_trace.jsonl in a footer line.
            self.assertIn("agent_trace.jsonl", report)

    def test_agent_openai_planner_rejects_unsupported_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_MEMORY_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_TOOL_CALLS"] = json.dumps([{"name": "shell", "arguments": {"cmd": "pwd"}}])
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "agent",
                    "--planner",
                    "openai",
                    "--provider",
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
            env["AISWMM_MEMORY_DIR"] = tmp
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
                    "--provider",
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
            env["AISWMM_MEMORY_DIR"] = tmp
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
                    "--provider",
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
            # Runtime UX PRD: inline allowed_tools list is gone; the
            # tool names below still appear in stdout (the executor
            # progress channel) but no longer in the final report.
            self.assertIn("agent_trace.jsonl", report)

    def test_search_files_normalizes_recursive_extension_glob(self) -> None:
        registry = AgentToolRegistry()
        with tempfile.TemporaryDirectory() as tmp:
            result = registry.execute(ToolCall("search_files", {"query": "[OPTIONS]", "glob": "**.inp"}), Path(tmp))

        self.assertTrue(result["ok"])
        self.assertEqual(result["glob"], "**/*.inp")
        self.assertGreaterEqual(len(result["results"]), 2)

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
        # LLM-driven dispatch refactor: ``synth_swmm_from_bbox`` is the
        # new typed entry point that replaces the ``select_workflow_mode``
        # gate in the capabilities surface.
        self.assertIn("synth_swmm_from_bbox", payload["tools"])
        self.assertIn("inspect_plot_options", payload["tools"])
        self.assertIn("apply_patch", payload["tools"])
        self.assertIn("run_tests", payload["tools"])
        self.assertIn("run_allowed_command", payload["tools"])

    def test_plot_help_exposes_node_attribute_selection(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "plot", "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("--node-attr", proc.stdout)
        self.assertIn("Volume_stored_ponded", proc.stdout)

    def test_agent_blocks_disallowed_shell_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_MEMORY_DIR"] = tmp
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
                    "--provider",
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
            env["AISWMM_MEMORY_DIR"] = tmp
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
                    "--provider",
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
            # PRD-185: the default digest renderer collapses the
            # legacy '[N] tool' + 'OK: <summary>' pair onto a single
            # line that carries the ✓ marker after a Y/n stamp.
            self.assertIn("✓", proc.stdout)

    def test_openai_agent_writes_session_state_and_context_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "agent-session"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_MEMORY_DIR"] = tmp
            env["AISWMM_OPENAI_MOCK_RESPONSE"] = "state checked"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentic_swmm.cli",
                    "agent",
                    "--planner",
                    "openai",
                    "--provider",
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
            self.assertIn("intent_contracts", state)
            self.assertIn("workflow_state", state)
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
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"AISWMM_CONFIG_DIR": tmp}), patch("agentic_swmm.agent.mcp_client.list_tools", return_value=fake_tools) as mocked:
            result = registry.execute(ToolCall("list_mcp_tools", {"server": "swmm-network"}), Path(tmp))

        self.assertTrue(result["ok"])
        self.assertEqual(mocked.call_args.kwargs["timeout"], 5)
        self.assertEqual(result["mapped_tools"][0]["planner_tool"], "call_mcp_tool")
        self.assertEqual(result["mapped_tools"][0]["arguments"]["server"], "swmm-network")
        self.assertIn("network_json", result["mapped_tools"][0]["arguments"]["arguments_schema"]["properties"])

    def test_mcp_tool_list_uses_schema_cache(self) -> None:
        registry = AgentToolRegistry()
        fake_tools = [{"name": "cached_run", "description": "Cached run.", "inputSchema": {"type": "object", "properties": {}}}]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"AISWMM_CONFIG_DIR": tmp}), patch("agentic_swmm.agent.mcp_client.list_tools", return_value=fake_tools) as mocked:
            first = registry.execute(ToolCall("list_mcp_tools", {"server": "swmm-runner"}), Path(tmp))
            second = registry.execute(ToolCall("list_mcp_tools", {"server": "swmm-runner"}), Path(tmp))

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(first["cache"], "miss")
        self.assertEqual(second["cache"], "hit")
        self.assertEqual(mocked.call_count, 1)

    def test_mcp_tool_list_refresh_bypasses_schema_cache(self) -> None:
        registry = AgentToolRegistry()
        fake_tools = [{"name": "cached_run", "description": "Cached run.", "inputSchema": {"type": "object", "properties": {}}}]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"AISWMM_CONFIG_DIR": tmp}), patch("agentic_swmm.agent.mcp_client.list_tools", return_value=fake_tools) as mocked:
            registry.execute(ToolCall("list_mcp_tools", {"server": "swmm-runner"}), Path(tmp))
            refreshed = registry.execute(ToolCall("list_mcp_tools", {"server": "swmm-runner", "refresh": True}), Path(tmp))

        self.assertTrue(refreshed["ok"])
        self.assertEqual(refreshed["cache"], "refresh")
        self.assertEqual(mocked.call_count, 2)

    def test_mcp_tool_list_honors_short_timeout(self) -> None:
        registry = AgentToolRegistry()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"AISWMM_CONFIG_DIR": tmp}), patch("agentic_swmm.agent.mcp_client.list_tools", side_effect=RuntimeError("MCP response timed out.")) as mocked:
            result = registry.execute(ToolCall("list_mcp_tools", {"server": "swmm-runner", "timeout_seconds": 3}), Path(tmp))

        self.assertFalse(result["ok"])
        self.assertEqual(mocked.call_args.kwargs["timeout"], 3)
        self.assertIn("fallback_tools", result)
        self.assertIn("run_swmm_inp", result["fallback_tools"])

    def test_mcp_call_failure_reports_recovery_and_fallback(self) -> None:
        registry = AgentToolRegistry()
        with tempfile.TemporaryDirectory() as tmp, patch("agentic_swmm.agent.mcp_client.call_tool", side_effect=RuntimeError("bad args")):
            result = registry.execute(ToolCall("call_mcp_tool", {"server": "swmm-runner", "tool": "run", "arguments": {}}), Path(tmp))

        self.assertFalse(result["ok"])
        self.assertIn("recovery", result)
        self.assertIn("run_swmm_inp", result["fallback_tools"])

    def test_agent_can_inspect_plot_options_before_plotting(self) -> None:
        registry = AgentToolRegistry()
        inp = REPO_ROOT / "examples" / "tecnopolo" / "tecnopolo_r1_199401.inp"
        with tempfile.TemporaryDirectory() as tmp:
            result = registry.execute(ToolCall("inspect_plot_options", {"inp_path": str(inp)}), Path(tmp))

        payload = result["results"]
        self.assertTrue(result["ok"])
        self.assertEqual(payload["defaults"]["node"], "OU2")
        self.assertTrue(payload["rainfall_options"])
        self.assertNotIn("rain_ts", payload["selections_needed"])
        self.assertIn("node", payload["selections_needed"])
        self.assertIn("node_attr", payload["selections_needed"])
        self.assertIn("Total_inflow", {item["name"] for item in payload["node_attribute_options"]})
        self.assertIn("Flow_lost_flooding", {item["name"] for item in payload["node_attribute_options"]})

    def test_agent_openai_planner_reports_missing_external_inp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside_inp = Path(tmp) / "outside.inp"
            env = os.environ.copy()
            env["AISWMM_CONFIG_DIR"] = tmp
            env["AISWMM_MEMORY_DIR"] = tmp
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
                    "--provider",
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
            env["AISWMM_MEMORY_DIR"] = tmp
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
            expected_skills = sum(
                1
                for entry in (REPO_ROOT / "skills").iterdir()
                if entry.is_dir() and (entry / "SKILL.md").is_file()
            )
            self.assertEqual(payload["resources"]["skills"], expected_skills)
            expected_mcp_servers = sum(
                1
                for entry in (REPO_ROOT / "mcp").iterdir()
                if entry.is_dir() and (entry / "server.js").is_file()
            )
            self.assertEqual(payload["resources"]["mcp_servers"], expected_mcp_servers)
            # #79 P1-1 expanded LONG_TERM_MEMORY_FILES from 3 to 7. Use
            # dynamic counts from the runtime registry so future expansions
            # don't drift the test.
            from agentic_swmm.runtime.registry import (
                LONG_TERM_MEMORY_FILES as _LONG_TERM_MEMORY_FILES,
                MODELING_MEMORY_FILES as _MODELING_MEMORY_FILES,
            )
            expected_long_term = len(_LONG_TERM_MEMORY_FILES)
            expected_project_modeling = len(_MODELING_MEMORY_FILES)
            self.assertEqual(
                payload["resources"]["memory_files"],
                expected_long_term + expected_project_modeling,
            )
            self.assertEqual(
                payload["resources"]["memory_layers"]["long_term"],
                expected_long_term,
            )
            self.assertEqual(
                payload["resources"]["memory_layers"]["project_modeling"],
                expected_project_modeling,
            )
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

            audit_dir = run_dir / "09_audit"
            self.assertTrue((audit_dir / "experiment_provenance.json").exists())
            self.assertTrue((audit_dir / "comparison.json").exists())
            self.assertTrue((audit_dir / "experiment_note.md").exists())
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
            audit_dir = run_dir / "09_audit"
            provenance = json.loads((audit_dir / "experiment_provenance.json").read_text(encoding="utf-8"))
            note = (audit_dir / "experiment_note.md").read_text(encoding="utf-8")
            self.assertEqual(provenance["workflow_mode"], "external_inp_import")
            self.assertIn("Workflow mode | external_inp_import", note)
            self.assertIn("External INP imports are copied into the run directory before execution", note)


if __name__ == "__main__":
    unittest.main()
