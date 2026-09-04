"""A declined tool call ends the turn; the planner does not ask again (F-127).

Live finding 2026-09-03 (scenario S58, "n" to the fetch): the planner asked
for fetch_swmm_from_canada twice more with other areas (a city, a smaller
bbox) before giving up, three prompts for one "no", and then described the
decline as "blocked by the runtime".
"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agentic_swmm.agent import planner as planner_module
from agentic_swmm.agent.executor import DENIED_SUMMARY
from agentic_swmm.agent.planner import Planner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse

NON_SWMM_GOAL = "tell me about this repository"


class _ScriptedProvider:
    def __init__(self, responses: list[ProviderToolResponse]) -> None:
        self._responses = list(responses)

    def respond_with_tools(self, *, system_prompt, input_items, tools, previous_response_id=None) -> ProviderToolResponse:
        if not self._responses:
            raise AssertionError("the planner asked for another response after the decline")
        return self._responses.pop(0)


class _DenyingExecutor:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.dry_run = False
        self.recorded: list[ToolCall] = []

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        self.recorded.append(call)
        result = {"tool": call.name, "args": call.args, "ok": False, "summary": DENIED_SUMMARY, "permission": {"prompted": True, "approved": False}}
        self.results.append(result)
        return result


def _tool_call(name: str, args: dict[str, Any], *, call_id: str) -> ProviderToolCall:
    return ProviderToolCall(call_id=call_id, name=name, arguments=args)


def _tool_response(calls: list[ProviderToolCall], *, response_id: str) -> ProviderToolResponse:
    return ProviderToolResponse(text="", model="stub", response_id=response_id, tool_calls=calls, raw={})


class DeclinedCallEndsTheTurnTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(planner_module, "_stdin_is_interactive", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_no_ends_the_turn_without_another_prompt(self) -> None:
        provider = _ScriptedProvider(
            [
                _tool_response([_tool_call("fetch_swmm_from_canada", {"bbox": [-123.38, 48.42, -123.35, 48.44], "start_date": "2023-11-01"}, call_id="c1")], response_id="r1"),
                # Never reached: the second attempt with another area.
                _tool_response([_tool_call("fetch_swmm_from_canada", {"city": "Victoria"}, call_id="c2")], response_id="r2"),
            ]
        )
        executor = _DenyingExecutor()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            outcome = Planner(
                provider=provider,  # type: ignore[arg-type]
                registry=AgentToolRegistry(),
                max_steps=8,
                verbose=False,
                emit=lambda text: None,
                progress_stream=io.StringIO(),
            ).run(goal=NON_SWMM_GOAL, session_dir=session_dir, trace_path=trace_path, executor=executor)
            trace_text = trace_path.read_text(encoding="utf-8")
        self.assertFalse(outcome.ok)
        self.assertEqual(len(executor.recorded), 1)
        self.assertIn("You declined fetch_swmm_from_canada", outcome.final_text)
        self.assertIn("bbox=", outcome.final_text)
        self.assertIn("nothing ran this turn", outcome.final_text)
        self.assertIn('"event": "planner_declined"', trace_text)

    def test_a_denial_is_recognised_by_permission_or_summary(self) -> None:
        self.assertTrue(planner_module._is_user_denial({"ok": False, "summary": DENIED_SUMMARY}))
        self.assertTrue(planner_module._is_user_denial({"ok": False, "summary": "x", "permission": {"prompted": True, "approved": False}}))
        self.assertFalse(planner_module._is_user_denial({"ok": False, "summary": "missing file", "permission": {"prompted": True, "approved": True}}))
        self.assertFalse(planner_module._is_user_denial({"ok": False, "summary": "missing file"}))
