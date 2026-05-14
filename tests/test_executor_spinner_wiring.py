"""Wiring test: AgentExecutor drives a Spinner on the progress stream.

PRD_runtime Done Criteria: ``test_executor_uses_spinner`` — run the
executor with ``stdout.isatty()`` faked to True against a 3-tool
fixture plan, capture stdout, assert it contains ``\\r`` carriage
returns and the final on-screen state shows each tool's label exactly
once (no ``[i/N] toolname`` scroll lines).
"""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


class _FakeTTYStream(io.StringIO):
    def isatty(self) -> bool:  # type: ignore[override]
        return True


class _CannedRegistry(AgentToolRegistry):
    def execute(self, call: ToolCall, session_dir: Path) -> dict[str, Any]:
        return {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}


class ExecutorUsesSpinnerTests(unittest.TestCase):
    def test_executor_uses_spinner_carriage_return_three_tools(self) -> None:
        registry = _CannedRegistry()
        stream = _FakeTTYStream()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            executor = AgentExecutor(
                registry,
                session_dir=session_dir,
                trace_path=session_dir / "agent_trace.jsonl",
                # QUICK + tools that are either read-only or whose
                # ``permissions.prompt_user`` will short-circuit to True
                # because stdin isn't a TTY in tests.
                profile=Profile.QUICK,
                progress_stream=stream,
            )
            for index, call in enumerate(
                [
                    ToolCall("read_file", {"path": "README.md"}),
                    ToolCall("list_skills", {}),
                    ToolCall("read_skill", {"skill_name": "swmm-runner"}),
                ],
                start=1,
            ):
                executor.execute(call, index=index)
            executor.close()

        output = stream.getvalue()
        self.assertIn("\r", output, "spinner must emit carriage returns")
        for label in ("read_file", "list_skills", "read_skill"):
            self.assertEqual(
                output.count(label),
                1,
                f"label {label!r} must appear exactly once; got {output.count(label)} times",
            )
        # The legacy "[i/N] toolname" scroll style must not appear.
        self.assertNotIn("[1/3]", output)
        self.assertNotIn("[2/3]", output)


if __name__ == "__main__":
    unittest.main()
