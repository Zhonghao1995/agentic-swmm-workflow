"""Unit tests for ``Profile`` and integration with the executor.

PRD_runtime "Module: Permissions profile":
- ``Profile = Enum{SAFE, QUICK}``; default ``SAFE``.
- ``Profile.QUICK.auto_approve(tool_name, registry) -> bool`` returns
  ``True`` only when ``registry.is_read_only(tool_name)``.

PRD wiring test ``test_quick_profile_skips_prompt`` lives at the bottom
of this file: it invokes the executor in QUICK mode and confirms the
prompt is bypassed for read-only tools but invoked for write tools.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agentic_swmm.agent import permissions
from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


class ProfileAutoApproveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AgentToolRegistry()

    def test_quick_auto_approves_read_only_tool(self) -> None:
        self.assertTrue(Profile.QUICK.auto_approve("read_file", self.registry))
        self.assertTrue(Profile.QUICK.auto_approve("list_skills", self.registry))

    def test_quick_does_not_auto_approve_write_tool(self) -> None:
        self.assertFalse(Profile.QUICK.auto_approve("plot_run", self.registry))
        self.assertFalse(Profile.QUICK.auto_approve("run_swmm_inp", self.registry))

    def test_quick_does_not_auto_approve_unknown_tool(self) -> None:
        self.assertFalse(Profile.QUICK.auto_approve("definitely-not-a-tool", self.registry))

    def test_safe_never_auto_approves(self) -> None:
        # SAFE always prompts (auto_approve always False), so the
        # default profile stays conservative.
        self.assertFalse(Profile.SAFE.auto_approve("read_file", self.registry))
        self.assertFalse(Profile.SAFE.auto_approve("plot_run", self.registry))


# ---------------------------------------------------------------------------
# Wiring test (PRD ``test_quick_profile_skips_prompt``)
# ---------------------------------------------------------------------------


class _RecordingRegistry(AgentToolRegistry):
    """Tiny subclass that records executions instead of running tools."""

    def __init__(self) -> None:
        super().__init__()
        self.executed: list[ToolCall] = []

    def execute(self, call: ToolCall, session_dir: Path) -> dict[str, Any]:
        self.executed.append(call)
        return {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}


class QuickProfileSkipsPromptWiringTests(unittest.TestCase):
    def test_quick_profile_skips_prompt_for_read_only_calls_for_write(self) -> None:
        registry = _RecordingRegistry()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            executor = AgentExecutor(
                registry,
                session_dir=session_dir,
                trace_path=session_dir / "agent_trace.jsonl",
                dry_run=False,
                profile=Profile.QUICK,
            )

            with mock.patch.object(
                permissions, "prompt_user", return_value=True
            ) as patched:
                # Read-only tool: must be auto-approved, no prompt.
                executor.execute(ToolCall("read_file", {"path": "README.md"}), index=1)
                self.assertEqual(
                    patched.call_count,
                    0,
                    "prompt_user must NOT be called for read-only tools under QUICK",
                )

                # Write tool: prompt must fire.
                executor.execute(
                    ToolCall("plot_run", {"run_dir": "runs/agent"}), index=2
                )
                self.assertEqual(
                    patched.call_count,
                    1,
                    "prompt_user must be called for write tools under QUICK",
                )
                ((tool_name,), _kwargs) = patched.call_args
                self.assertEqual(tool_name, "plot_run")


if __name__ == "__main__":
    unittest.main()
