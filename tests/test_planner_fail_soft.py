"""Regression: planner must not hard-stop on a single tool failure.

Motivation: a real session at ``runs/2026-05-13/224244_tecnopolo_run/`` saw
``plot_run`` fail with ``unrecognized arguments: --auto-window-mode flow-peak``
and the planner terminated the session before the LLM ever saw the
failure payload. The fix feeds the failure result back as the next
round's ``input_items`` so the LLM can retry, pivot, or report
gracefully. A same-tool-retry guard (3 consecutive failures of the same
tool name) bounds the loop so we cannot spin forever.

These tests pin the new contract:

* ``test_planner_feeds_failure_to_llm`` — failure output is fed to the
  next ``respond_with_tools`` call.
* ``test_planner_retries_pivot`` — after a failure, the LLM may emit a
  different tool that succeeds and the session continues to a final
  answer.
* ``test_planner_same_tool_retry_guard`` — three consecutive failures of
  the same tool name give up with ``planner_giveup`` in the trace.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


# A goal that does NOT look like a SWMM request and has no
# active_run_dir in prior_state — this keeps the planner from short-
# circuiting through the workflow router or plot-continuation classifier
# and forces it into the OpenAI agent loop we want to exercise.
NON_SWMM_GOAL = "tell me about this repository"


class _ScriptedProvider:
    """Provider stub that returns a pre-baked list of
    ``ProviderToolResponse`` instances in order."""

    def __init__(self, responses: list[ProviderToolResponse]) -> None:
        self._responses = list(responses)
        self.calls_received: list[list[dict[str, Any]]] = []

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        self.calls_received.append(list(input_items))
        if not self._responses:
            raise AssertionError(
                "scripted provider exhausted; planner asked for more responses than the test scripted"
            )
        return self._responses.pop(0)


class _ScriptedExecutor:
    """Executor stub that returns canned results keyed by tool name.

    Each tool name maps to a list of results, consumed in order. Tools
    without an entry get a generic ``ok=True`` result.
    """

    def __init__(self, tool_results: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._tool_results = tool_results or {}
        self.results: list[dict[str, Any]] = []
        self.dry_run = False
        self.recorded: list[ToolCall] = []

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        self.recorded.append(call)
        queue = self._tool_results.get(call.name)
        if queue:
            result = dict(queue.pop(0))
            result.setdefault("tool", call.name)
            result.setdefault("args", call.args)
        else:
            result = {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}
        self.results.append(result)
        return result


def _tool_call(name: str, args: dict[str, Any] | None = None, *, call_id: str | None = None) -> ProviderToolCall:
    return ProviderToolCall(call_id=call_id or f"call_{name}", name=name, arguments=args or {})


def _final(text: str) -> ProviderToolResponse:
    return ProviderToolResponse(
        text=text,
        model="stub",
        response_id="stub-final",
        tool_calls=[],
        raw={},
    )


def _tool_response(calls: list[ProviderToolCall], *, response_id: str = "stub-step") -> ProviderToolResponse:
    return ProviderToolResponse(
        text="",
        model="stub",
        response_id=response_id,
        tool_calls=calls,
        raw={},
    )


def _planner(provider: _ScriptedProvider) -> OpenAIPlanner:
    return OpenAIPlanner(
        provider=provider,  # type: ignore[arg-type]
        registry=AgentToolRegistry(),
        max_steps=8,
        verbose=False,
        emit=lambda text: None,
    )


class PlannerFailSoftTests(unittest.TestCase):
    def test_planner_feeds_failure_to_llm(self) -> None:
        # First round: model asks to read a file. Tool returns failure.
        # Second round: model emits no tool_calls and a final message —
        # this isolates the assertion (we just want to see that the
        # second respond_with_tools call received the failure payload).
        provider = _ScriptedProvider(
            [
                _tool_response([_tool_call("read_file", {"path": "no.txt"}, call_id="c1")]),
                _final("acknowledged the failure"),
            ]
        )
        executor = _ScriptedExecutor(
            {
                "read_file": [{"ok": False, "summary": "boom", "stderr_tail": "boom"}],
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            outcome = _planner(provider).run(
                goal=NON_SWMM_GOAL,
                session_dir=session_dir,
                trace_path=session_dir / "agent_trace.jsonl",
                executor=executor,
            )

        # The second call to respond_with_tools must have happened — the
        # planner is alive after a failure.
        self.assertEqual(len(provider.calls_received), 2)
        second_input = provider.calls_received[1]
        # And it must carry the failed tool's output as a
        # function_call_output whose serialized payload includes the
        # stderr_tail so the LLM has actionable evidence. The output is
        # a JSON string nested in another JSON object, so we parse it
        # back rather than relying on substring shape.
        self.assertEqual(len(second_input), 1)
        item = second_input[0]
        self.assertEqual(item.get("type"), "function_call_output")
        self.assertEqual(item.get("call_id"), "c1")
        payload = json.loads(item.get("output", "{}"))
        self.assertEqual(payload.get("ok"), False)
        self.assertEqual(payload.get("stderr_tail"), "boom")
        # The session terminates cleanly with the model's final text.
        self.assertEqual(outcome.final_text, "acknowledged the failure")

    def test_planner_retries_pivot(self) -> None:
        # Round 1: read_file fails.
        # Round 2: model pivots to list_dir, which succeeds.
        # Round 3: model emits final text, no tool_calls.
        provider = _ScriptedProvider(
            [
                _tool_response(
                    [_tool_call("read_file", {"path": "no.txt"}, call_id="c1")],
                    response_id="r1",
                ),
                _tool_response(
                    [_tool_call("list_dir", {"path": "."}, call_id="c2")],
                    response_id="r2",
                ),
                _final("done"),
            ]
        )
        executor = _ScriptedExecutor(
            {
                "read_file": [{"ok": False, "summary": "missing", "stderr_tail": "no such file"}],
                "list_dir": [{"ok": True, "summary": "listed"}],
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            outcome = _planner(provider).run(
                goal=NON_SWMM_GOAL,
                session_dir=session_dir,
                trace_path=session_dir / "agent_trace.jsonl",
                executor=executor,
            )

        self.assertTrue(outcome.ok, f"planner should have recovered; got final_text={outcome.final_text!r}")
        self.assertEqual([c.name for c in outcome.plan], ["read_file", "list_dir"])
        self.assertEqual(outcome.final_text, "done")

    def test_planner_same_tool_retry_guard(self) -> None:
        # Provider keeps asking for the same failing tool.
        # The planner must give up after 3 consecutive failures of the
        # same tool name. A 4th respond_with_tools call would mean the
        # guard is missing.
        provider = _ScriptedProvider(
            [
                _tool_response(
                    [_tool_call("read_file", {"path": "x.txt"}, call_id=f"c{i}")],
                    response_id=f"r{i}",
                )
                for i in range(1, 5)
            ]
        )
        executor = _ScriptedExecutor(
            {
                "read_file": [{"ok": False, "summary": "boom", "stderr_tail": "boom"}] * 4,
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            outcome = _planner(provider).run(
                goal=NON_SWMM_GOAL,
                session_dir=session_dir,
                trace_path=trace_path,
                executor=executor,
            )

            # The planner must not have called the provider a 4th time.
            self.assertEqual(
                len(provider.calls_received),
                3,
                f"planner should stop asking the provider after 3 consecutive same-tool failures; got {len(provider.calls_received)} provider calls",
            )
            self.assertFalse(outcome.ok)
            self.assertIn("giving up", outcome.final_text.lower())
            # The trace records the give-up event so an auditor can see
            # the loop was bounded by policy, not crashed.
            trace_text = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
            self.assertIn("planner_giveup", trace_text)


if __name__ == "__main__":
    unittest.main()
