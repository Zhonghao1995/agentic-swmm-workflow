"""Issue #355: cumulative-failure checkpoint for the OpenAI agent loop.

Motivation: a real session (``runs/2026-07-29/171531_100_chat`` in the
reporter's checkout) chained six failures across *different* tools —
``synth_swmm_from_bbox`` (missing extra), ``web_search`` (no network),
``apply_patch`` (policy gate, then corrupt diff), ``run_allowed_command``
(not allowlisted) — and the planner silently pivoted past each one until
``max_steps=40`` killed the turn with no summary of what blocked it.

The pre-existing guard (``SAME_TOOL_RETRY_LIMIT``) only counts
consecutive failures of the *same* tool name: pivoting to a different
tool resets it to 1 and any success clears it, so a turn interleaving
failures with successful ``list_dir``/``read_file`` probes never trips
it. These tests pin the new contract:

* cumulative failures within a turn are never reset by pivots or
  interleaved successes;
* at ``PIVOT_CHECKPOINT_LIMIT`` failures the planner pauses — headless
  runs stop immediately (fail closed, ``AISWMM_HITL_AUTO_APPROVE=1``
  continues), interactive runs are asked once with the blocker
  inventory and the proposed next tools;
* ``max_steps`` exhaustion names the unresolved blockers instead of
  the bare ``planner stopped after max_steps=N``;
* the same-tool give-up guard keeps precedence over the checkpoint.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agentic_swmm.agent import planner as planner_module
from agentic_swmm.agent.planner import Planner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


# Mirrors tests/test_planner_fail_soft.py: a goal that does not look
# like a SWMM request so the planner goes straight to the OpenAI loop.
NON_SWMM_GOAL = "tell me about this repository"


class _ScriptedProvider:
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


def _tool_call(name: str, args: dict[str, Any] | None = None, *, call_id: str) -> ProviderToolCall:
    return ProviderToolCall(call_id=call_id, name=name, arguments=args or {})


def _final(text: str) -> ProviderToolResponse:
    return ProviderToolResponse(text=text, model="stub", response_id="stub-final", tool_calls=[], raw={})


def _tool_response(calls: list[ProviderToolCall], *, response_id: str = "stub-step") -> ProviderToolResponse:
    return ProviderToolResponse(text="", model="stub", response_id=response_id, tool_calls=calls, raw={})


def _planner(provider: _ScriptedProvider, *, max_steps: int = 8, progress_stream: io.StringIO | None = None) -> Planner:
    return Planner(
        provider=provider,  # type: ignore[arg-type]
        registry=AgentToolRegistry(),
        max_steps=max_steps,
        verbose=False,
        emit=lambda text: None,
        progress_stream=progress_stream,
    )


def _fail(summary: str) -> dict[str, Any]:
    return {"ok": False, "summary": summary, "stderr_tail": summary}


# The reported session's shape: three *different* tools fail, one per
# step, mirroring synth → web → patch pivots.
def _three_distinct_failures() -> tuple[_ScriptedProvider, _ScriptedExecutor]:
    provider = _ScriptedProvider(
        [
            _tool_response([_tool_call("read_file", {"path": "a.txt"}, call_id="c1")], response_id="r1"),
            _tool_response([_tool_call("apply_patch", {"patch": "x"}, call_id="c2")], response_id="r2"),
            _tool_response([_tool_call("run_allowed_command", {"command": "x"}, call_id="c3")], response_id="r3"),
            # Headless runs must never reach this 4th response; an
            # interactive "continue" executes it and closes cleanly.
            _tool_response([_tool_call("list_dir", {"path": "."}, call_id="c4")], response_id="r4"),
            _final("closing text after recovery"),
        ]
    )
    executor = _ScriptedExecutor(
        {
            "read_file": [_fail("missing file")],
            "apply_patch": [_fail("corrupt patch at line 11")],
            "run_allowed_command": [_fail("command not allowlisted")],
        }
    )
    return provider, executor


class PlannerFailureCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        # Deterministic headless default: pytest's stdin is usually not
        # a TTY, but pin it so the suite does not depend on the runner.
        patcher = mock.patch.object(planner_module, "_stdin_is_interactive", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Never inherit the operator's env into checkpoint decisions.
        env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop("AISWMM_HITL_AUTO_APPROVE", None)

    def _run(self, provider: _ScriptedProvider, executor: _ScriptedExecutor, *, max_steps: int = 8, progress_stream: io.StringIO | None = None):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            outcome = _planner(provider, max_steps=max_steps, progress_stream=progress_stream).run(
                goal=NON_SWMM_GOAL,
                session_dir=session_dir,
                trace_path=trace_path,
                executor=executor,
            )
            trace_text = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
        return outcome, trace_text

    def test_distinct_tool_failures_stop_headless(self) -> None:
        provider, executor = _three_distinct_failures()
        outcome, trace_text = self._run(provider, executor)

        self.assertEqual(
            len(provider.calls_received),
            3,
            "headless planner must stop at the failure checkpoint, not ask the provider again",
        )
        self.assertFalse(outcome.ok)
        self.assertIn("failure checkpoint", outcome.final_text)
        for tool in ("read_file", "apply_patch", "run_allowed_command"):
            self.assertIn(tool, outcome.final_text)
        # The stop message must name the switch that re-enables long
        # autonomous headless turns.
        self.assertIn("AISWMM_HITL_AUTO_APPROVE", outcome.final_text)
        self.assertIn("planner_failure_checkpoint", trace_text)
        self.assertIn("headless_stop", trace_text)

    def test_success_between_failures_does_not_reset_checkpoint(self) -> None:
        # The exact hole from the reported session: successful probe
        # calls interleave with the failures. The same-tool guard is
        # reset by them; the cumulative checkpoint must not be.
        provider = _ScriptedProvider(
            [
                _tool_response([_tool_call("read_file", {"path": "a"}, call_id="c1")], response_id="r1"),
                _tool_response([_tool_call("list_dir", {"path": "."}, call_id="c2")], response_id="r2"),
                _tool_response([_tool_call("apply_patch", {"patch": "x"}, call_id="c3")], response_id="r3"),
                _tool_response([_tool_call("search_files", {"query": "x"}, call_id="c4")], response_id="r4"),
                _tool_response([_tool_call("run_allowed_command", {"command": "x"}, call_id="c5")], response_id="r5"),
                _final("should never be reached"),
            ]
        )
        executor = _ScriptedExecutor(
            {
                "read_file": [_fail("missing file")],
                "apply_patch": [_fail("corrupt patch")],
                "run_allowed_command": [_fail("not allowlisted")],
            }
        )
        outcome, trace_text = self._run(provider, executor)

        self.assertEqual(len(provider.calls_received), 5)
        self.assertFalse(outcome.ok)
        self.assertIn("failure checkpoint", outcome.final_text)
        self.assertIn("planner_failure_checkpoint", trace_text)

    def test_auto_approve_env_continues_and_resets_window(self) -> None:
        # Six failures with the switch set: the planner passes two
        # checkpoints (at 3 and at 6) and reaches the model's final
        # text. Two trace events prove the window resets after each.
        names = ["read_file", "apply_patch", "run_allowed_command", "web_fetch_url", "network_qa", "git_diff"]
        provider = _ScriptedProvider(
            [
                _tool_response([_tool_call(name, {}, call_id=f"c{i}")], response_id=f"r{i}")
                for i, name in enumerate(names, start=1)
            ]
            + [_final("gave up in prose")]
        )
        executor = _ScriptedExecutor({name: [_fail(f"{name} blocked")] for name in names})

        with mock.patch.dict(os.environ, {"AISWMM_HITL_AUTO_APPROVE": "1"}):
            outcome, trace_text = self._run(provider, executor, max_steps=10)

        self.assertEqual(len(provider.calls_received), 7)
        self.assertEqual(outcome.final_text, "gave up in prose")
        # Unrecovered failures still refuse to report success (P1-7).
        self.assertFalse(outcome.ok)
        self.assertEqual(trace_text.count("planner_failure_checkpoint"), 2)
        self.assertIn("auto_approve", trace_text)

    def test_interactive_prompt_continue(self) -> None:
        # Interactive turn: after 3 failures the user is shown the
        # blockers plus the proposed next tool and answers yes. The
        # planner executes the proposed tool and finishes cleanly.
        provider, executor = _three_distinct_failures()
        stream = io.StringIO()
        with mock.patch.object(planner_module, "_stdin_is_interactive", return_value=True), mock.patch.object(
            planner_module, "_prompt_continue_past_failures", return_value=True
        ) as prompt:
            outcome, trace_text = self._run(provider, executor, progress_stream=stream)

        self.assertEqual(prompt.call_count, 1)
        # list_dir (the proposed 4th tool) succeeded, then the final
        # text closed the turn: 5 provider calls, clean outcome.
        self.assertEqual(len(provider.calls_received), 5)
        self.assertTrue(outcome.ok, f"expected recovery after user continue; got {outcome.final_text!r}")
        banner = stream.getvalue()
        for tool in ("read_file", "apply_patch", "run_allowed_command"):
            self.assertIn(tool, banner)
        self.assertIn("list_dir", banner, "banner must show the proposed next tools")
        self.assertIn("user_continue", trace_text)

    def test_interactive_prompt_stop(self) -> None:
        provider, executor = _three_distinct_failures()
        with mock.patch.object(planner_module, "_stdin_is_interactive", return_value=True), mock.patch.object(
            planner_module, "_prompt_continue_past_failures", return_value=False
        ):
            outcome, trace_text = self._run(provider, executor)

        # The 4th response was fetched (to show the proposed pivot) but
        # its tool must NOT have executed.
        self.assertEqual(len(provider.calls_received), 4)
        self.assertEqual([c.name for c in executor.recorded], ["read_file", "apply_patch", "run_allowed_command"])
        self.assertFalse(outcome.ok)
        self.assertIn("failure checkpoint", outcome.final_text)
        self.assertIn("user_stop", trace_text)

    def test_max_steps_exhaustion_lists_blockers(self) -> None:
        # Two failures stay under the checkpoint; the turn dies on
        # max_steps=2 and must name the blockers instead of the bare
        # counter message.
        provider = _ScriptedProvider(
            [
                _tool_response([_tool_call("read_file", {"path": "a"}, call_id="c1")], response_id="r1"),
                _tool_response([_tool_call("apply_patch", {"patch": "x"}, call_id="c2")], response_id="r2"),
            ]
        )
        executor = _ScriptedExecutor(
            {
                "read_file": [_fail("missing file")],
                "apply_patch": [_fail("corrupt patch at line 11")],
            }
        )
        outcome, trace_text = self._run(provider, executor, max_steps=2)

        self.assertFalse(outcome.ok)
        self.assertIn("planner stopped after max_steps=2", outcome.final_text)
        self.assertIn("unresolved blockers", outcome.final_text)
        self.assertIn("read_file", outcome.final_text)
        self.assertIn("corrupt patch at line 11", outcome.final_text)
        self.assertIn("planner_max_steps_exhausted", trace_text)

    def test_same_tool_giveup_keeps_precedence(self) -> None:
        # Three consecutive failures of one tool trip the pre-existing
        # give-up guard, not the checkpoint: the stricter, older
        # contract stays first and the trace stays unambiguous.
        provider = _ScriptedProvider(
            [
                _tool_response([_tool_call("read_file", {"path": "x"}, call_id=f"c{i}")], response_id=f"r{i}")
                for i in range(1, 4)
            ]
        )
        executor = _ScriptedExecutor({"read_file": [_fail("boom")] * 3})
        outcome, trace_text = self._run(provider, executor)

        self.assertFalse(outcome.ok)
        self.assertIn("giving up", outcome.final_text.lower())
        self.assertIn("planner_giveup", trace_text)
        self.assertNotIn("planner_failure_checkpoint", trace_text)

    def test_clean_session_writes_no_checkpoint_events(self) -> None:
        provider = _ScriptedProvider(
            [
                _tool_response([_tool_call("list_dir", {"path": "."}, call_id="c1")], response_id="r1"),
                _final("done"),
            ]
        )
        executor = _ScriptedExecutor({"list_dir": [{"ok": True, "summary": "listed"}]})
        outcome, trace_text = self._run(provider, executor)

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.final_text, "done")
        self.assertNotIn("planner_failure_checkpoint", trace_text)
        self.assertNotIn("planner_max_steps_exhausted", trace_text)


if __name__ == "__main__":
    unittest.main()
