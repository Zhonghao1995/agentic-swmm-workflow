"""Planner replan injection for L5 (PRD-GF-L5).

After ``request_gap_judgement`` resolves with ``resume_mode="llm_replan"``
the planner runtime must inject a structured ``user_clarification``
message into the NEXT LLM turn's ``input_items``. The injection
carries the decision details (gap_kind, user_pick + summary,
user_note) so the planner LLM can re-plan with full context — adjust
the calibration window, narrow the tool call, etc.

These tests exercise the planner loop directly through a scripted
provider + scripted executor, mirroring the pattern in
``tests/test_planner_fail_soft.py``.
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
from agentic_swmm.gap_fill.protocol import (
    GapCandidate,
    GapDecision,
    ProposerInfo,
)
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


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
                "scripted provider exhausted; planner asked for more responses than scripted"
            )
        return self._responses.pop(0)


class _ScriptedExecutor:
    """Returns canned tool results in order; records calls."""

    def __init__(self, results_by_tool: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._results_by_tool = results_by_tool or {}
        self.results: list[dict[str, Any]] = []
        self.dry_run = False
        self.recorded: list[ToolCall] = []

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        self.recorded.append(call)
        queue = self._results_by_tool.get(call.name)
        if queue:
            result = dict(queue.pop(0))
            result.setdefault("tool", call.name)
            result.setdefault("args", call.args)
        else:
            result = {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}
        self.results.append(result)
        return result


def _tool_call(name: str, args: dict[str, Any], *, call_id: str = "c1") -> ProviderToolCall:
    return ProviderToolCall(call_id=call_id, name=name, arguments=args)


def _tool_response(calls: list[ProviderToolCall], *, response_id: str = "r1") -> ProviderToolResponse:
    return ProviderToolResponse(
        text="",
        model="stub",
        response_id=response_id,
        tool_calls=calls,
        raw={},
    )


def _final(text: str) -> ProviderToolResponse:
    return ProviderToolResponse(
        text=text,
        model="stub",
        response_id="stub-final",
        tool_calls=[],
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


def _seed_l5_decision(session_dir: Path, decision_id: str) -> None:
    """Pre-write an L5 decision into ``gap_decisions.json``.

    The handler would normally write this. For this test we seed it
    directly so the test stays focused on the planner-side injection
    contract (the handler is exercised in its own test file).

    We write the ledger directly rather than going through the
    GF-CORE recorder because the recorder rebuilds the GapDecision
    when populating ``human_decisions_ref`` and drops the L5
    extension fields. The production handler patches the file
    in-place after the recorder runs; here we just construct the
    final shape directly.
    """
    decision = GapDecision(
        decision_id=decision_id,
        gap_id="gap-test",
        severity="L5",
        field="storm_event_selection",
        proposer=ProposerInfo(
            source="human",
            confidence="HIGH",
            llm_call_id="enum-call-id",
        ),
        proposed_value=None,
        final_value="cand_2",
        proposer_overridden=False,
        decided_by="human",
        decided_at="2026-05-14T00:00:00Z",
        resume_mode="llm_replan",
        human_decisions_ref=None,
        gap_kind="storm_event_selection",
        candidates=(
            GapCandidate(
                id="cand_1",
                summary="2026-03-12 — 32 mm / 6 h",
                tradeoff="probes surface runoff",
            ),
            GapCandidate(
                id="cand_2",
                summary="2026-03-24 — 48 mm / 18 h",
                tradeoff="probes infiltration",
            ),
        ),
        user_pick="cand_2",
        user_note="Want infiltration-process calibration; long event better.",
        enumerator_llm_call_id="enum-call-id",
    )
    audit = session_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)
    ledger = audit / "gap_decisions.json"
    ledger.write_text(
        json.dumps(
            {"schema_version": "1", "decisions": [decision.to_dict()]},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


class ReplanInjectionTests(unittest.TestCase):
    def test_next_turn_input_contains_user_clarification(self) -> None:
        decision_id = "dec-test-replan-1"

        # Round 1: model calls request_gap_judgement (executor returns
        # the L5 result indicating replan).
        # Round 2: model emits final text — we just need to capture
        # what the planner injected into the second turn's input.
        provider = _ScriptedProvider(
            [
                _tool_response(
                    [
                        _tool_call(
                            "request_gap_judgement",
                            {
                                "gap_kind": "storm_event_selection",
                                "context": {"workflow": "calibrate"},
                                "evidence_ref": "06_qa/rainfall_event_summary.json",
                            },
                            call_id="c1",
                        )
                    ],
                    response_id="r1",
                ),
                _final("acknowledged the user's pick"),
            ]
        )
        executor = _ScriptedExecutor(
            {
                "request_gap_judgement": [
                    {
                        "ok": True,
                        "decision_id": decision_id,
                        "resume_mode": "llm_replan",
                        "gap_kind": "storm_event_selection",
                        "summary": "L5 judgement recorded",
                    }
                ]
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            _seed_l5_decision(session_dir, decision_id)
            _planner(provider).run(
                goal=NON_SWMM_GOAL,
                session_dir=session_dir,
                trace_path=session_dir / "agent_trace.jsonl",
                executor=executor,
            )

            self.assertEqual(len(provider.calls_received), 2)
            second_input = provider.calls_received[1]

            # The second turn must include both:
            #   - the function_call_output for the tool call, and
            #   - a user_clarification message with the decision
            #     details so the LLM can re-plan.
            types = [item.get("type") or item.get("role") for item in second_input]
            self.assertIn("function_call_output", types)
            clarification_items = [
                item
                for item in second_input
                if item.get("role") == "user"
                and "[gap_decision]" in (item.get("content") or "")
            ]
            self.assertEqual(
                len(clarification_items),
                1,
                f"expected exactly one user_clarification message; got {second_input}",
            )
            content = clarification_items[0]["content"]
            self.assertIn("gap_kind: storm_event_selection", content)
            self.assertIn("cand_2", content)
            self.assertIn("user_note", content)
            self.assertIn("Want infiltration", content)
            self.assertIn("re-plan", content.lower())

    def test_no_injection_when_resume_mode_absent(self) -> None:
        """A normal (non-L5) tool result must not inject a clarification."""
        provider = _ScriptedProvider(
            [
                _tool_response(
                    [_tool_call("read_file", {"path": "x"}, call_id="c1")],
                    response_id="r1",
                ),
                _final("done"),
            ]
        )
        executor = _ScriptedExecutor(
            {"read_file": [{"ok": True, "summary": "ok"}]}
        )

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            _planner(provider).run(
                goal=NON_SWMM_GOAL,
                session_dir=session_dir,
                trace_path=session_dir / "agent_trace.jsonl",
                executor=executor,
            )

            second_input = provider.calls_received[1]
            clarification_items = [
                item
                for item in second_input
                if item.get("role") == "user"
                and "[gap_decision]" in (item.get("content") or "")
            ]
            self.assertEqual(clarification_items, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
