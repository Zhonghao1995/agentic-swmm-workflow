"""Trace event: ``skill_selected`` is written when select_skill succeeds.

PRD-Y "skill_selected trace event":

    {"event": "skill_selected", "skill_name": "swmm-builder",
     "tool_count": 1, "timestamp_utc": "..."}

The event sits between ``session_start`` and the first concrete
``tool_call`` so audit notes can show which skill the agent committed
to before any deterministic-SWMM tool ran.
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


# Non-SWMM goal keeps the planner in the OpenAI agent loop (no
# auto-router short-circuit), so we can script the exact tool sequence.
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
            raise AssertionError("scripted provider exhausted")
        return self._responses.pop(0)


class _RealRegistryExecutor:
    """Minimal executor that defers to the real ``AgentToolRegistry`` so
    ``select_skill`` runs its actual handler (returns the swmm-builder
    tool subset).
    """

    def __init__(self, registry: AgentToolRegistry, session_dir: Path, trace_path: Path) -> None:
        from agentic_swmm.agent.reporting import write_event

        self._registry = registry
        self.session_dir = session_dir
        self.trace_path = trace_path
        self.results: list[dict[str, Any]] = []
        self.dry_run = False
        self.recorded: list[ToolCall] = []
        self._write_event = write_event

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        self.recorded.append(call)
        self._write_event(
            self.trace_path,
            {"event": "tool_start", "index": index, "tool": call.name, "args": call.args},
        )
        result = self._registry.execute(call, self.session_dir)
        self.results.append(result)
        self._write_event(
            self.trace_path,
            {"event": "tool_result", "index": index, **result},
        )
        return result


def _tool_call(name: str, args: dict[str, Any], *, call_id: str) -> ProviderToolCall:
    return ProviderToolCall(call_id=call_id, name=name, arguments=args)


def _final(text: str) -> ProviderToolResponse:
    return ProviderToolResponse(text=text, model="stub", response_id="final", tool_calls=[], raw={})


def _tool_response(calls: list[ProviderToolCall], *, response_id: str) -> ProviderToolResponse:
    return ProviderToolResponse(text="", model="stub", response_id=response_id, tool_calls=calls, raw={})


class SkillSelectedEventTests(unittest.TestCase):
    def test_skill_selected_event_written_when_select_skill_succeeds(self) -> None:
        provider = _ScriptedProvider(
            [
                _tool_response(
                    [_tool_call("select_skill", {"skill_name": "swmm-builder"}, call_id="c1")],
                    response_id="r1",
                ),
                _final("done"),
            ]
        )
        registry = AgentToolRegistry()
        planner = OpenAIPlanner(
            provider=provider,  # type: ignore[arg-type]
            registry=registry,
            max_steps=4,
            verbose=False,
            emit=lambda text: None,
        )

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            executor = _RealRegistryExecutor(registry, session_dir, trace_path)
            outcome = planner.run(
                goal=NON_SWMM_GOAL,
                session_dir=session_dir,
                trace_path=trace_path,
                executor=executor,  # type: ignore[arg-type]
            )

            events: list[dict[str, Any]] = []
            with trace_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    events.append(json.loads(line))

        self.assertTrue(outcome.ok)
        skill_events = [e for e in events if e.get("event") == "skill_selected"]
        self.assertEqual(
            len(skill_events),
            1,
            f"expected exactly one skill_selected event; got {skill_events}",
        )
        event = skill_events[0]
        self.assertEqual(event.get("skill_name"), "swmm-builder")
        # ``tool_count`` is the number of tools the planner now sees for
        # this skill — at least 1 (build_inp).
        self.assertGreaterEqual(event.get("tool_count", 0), 1)
        # The event carries an ISO-formatted UTC timestamp, like every
        # other trace event.
        self.assertIn("timestamp_utc", event)

    def test_skill_selected_event_not_written_on_failure(self) -> None:
        provider = _ScriptedProvider(
            [
                _tool_response(
                    [_tool_call("select_skill", {"skill_name": "not-a-real-skill"}, call_id="c1")],
                    response_id="r1",
                ),
                _final("acknowledged failure"),
            ]
        )
        registry = AgentToolRegistry()
        planner = OpenAIPlanner(
            provider=provider,  # type: ignore[arg-type]
            registry=registry,
            max_steps=4,
            verbose=False,
            emit=lambda text: None,
        )

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            executor = _RealRegistryExecutor(registry, session_dir, trace_path)
            planner.run(
                goal=NON_SWMM_GOAL,
                session_dir=session_dir,
                trace_path=trace_path,
                executor=executor,  # type: ignore[arg-type]
            )
            text = trace_path.read_text(encoding="utf-8")
        # Failed select_skill must not emit the success event.
        self.assertNotIn("skill_selected", text)


if __name__ == "__main__":
    unittest.main()
