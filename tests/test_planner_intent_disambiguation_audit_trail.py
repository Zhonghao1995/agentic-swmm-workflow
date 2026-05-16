"""Audit-trail wiring for the intent disambiguator (#111, user story 3).

Every disambiguation event must produce:

1. one ``intent_disambiguation`` line in ``agent_trace.jsonl`` carrying
   the goal, which conflict signals fired, the LLM's picked mode (or
   ``null`` on fallback), the wall-clock duration, and a
   ``fallback_used`` boolean;
2. one ``llm_calls.jsonl`` row under ``09_audit/`` with
   ``model_role="disambiguate_intent"`` so the LLM observer captures
   it next to the rest of the per-turn LLM trace.

These two artefacts let a paper reviewer (a) verify reproducibility
across reruns, and (b) quantify the % of turns that used LLM
intervention rather than the deterministic SOP.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


class _ScriptedProvider:
    """Returns a forced ``classify_workflow_mode`` call first, then a
    final-text response for the post-route OpenAI loop."""

    def __init__(self, picked_mode: str) -> None:
        self._mode = picked_mode
        self._call_index = 0

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        self._call_index += 1
        if self._call_index == 1:
            # First call is the disambiguator's fake-tool invocation.
            return ProviderToolResponse(
                text="",
                model="stub",
                response_id="r-disambig",
                tool_calls=[
                    ProviderToolCall(
                        call_id="c1",
                        name="classify_workflow_mode",
                        arguments={"mode": self._mode},
                    )
                ],
                raw={},
            )
        return ProviderToolResponse(
            text="done",
            model="stub",
            response_id="r-final",
            tool_calls=[],
            raw={},
        )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


class IntentDisambiguationAuditTrailTests(unittest.TestCase):
    def _run_planner(
        self,
        goal: str,
        picked_mode: str,
        tmp: Path,
    ) -> tuple[Path, Path]:
        trace_path = tmp / "agent_trace.jsonl"
        registry = AgentToolRegistry()
        executor = AgentExecutor(
            registry,
            session_dir=tmp,
            trace_path=trace_path,
            dry_run=False,
            profile=Profile.QUICK,
        )
        planner = OpenAIPlanner(
            provider=_ScriptedProvider(picked_mode),  # type: ignore[arg-type]
            registry=registry,
            max_steps=2,
            verbose=False,
            emit=lambda text: None,
        )
        planner.run(
            goal=goal,
            session_dir=tmp,
            trace_path=trace_path,
            executor=executor,
        )
        return trace_path, tmp / "09_audit" / "llm_calls.jsonl"

    def test_intent_disambiguation_trace_event_recorded(self) -> None:
        """The planner must write one ``intent_disambiguation`` event
        per disambiguation call, carrying the goal, conflict signals,
        picked mode, and a fallback flag."""

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            trace_path, _ = self._run_planner(
                goal="run Tod Creek demo and plot the figure",
                picked_mode="prepared_demo",
                tmp=tmp,
            )
            events = [
                row
                for row in _read_jsonl(trace_path)
                if row.get("event") == "intent_disambiguation"
            ]

        assert len(events) == 1, events
        event = events[0]
        assert event["picked_mode"] == "prepared_demo", event
        assert event["fallback_used"] is False, event
        assert event["goal"] == "run Tod Creek demo and plot the figure", event
        # Conflict signals must capture which ``wants_*`` flags fired.
        assert "wants_plot" in event["conflict_signals"], event
        assert "wants_demo" in event["conflict_signals"], event
        assert "duration_ms" in event, event
        assert isinstance(event["duration_ms"], int), event

    def test_llm_call_recorded_with_disambiguate_intent_role(self) -> None:
        """The disambiguator's LLM call must be funnelled through
        ``record_llm_call`` with ``model_role="disambiguate_intent"``
        so the symmetric LLM trace under ``09_audit/`` captures it."""

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, llm_jsonl = self._run_planner(
                goal="run Tod Creek demo and plot the figure",
                picked_mode="prepared_demo",
                tmp=tmp,
            )
            rows = _read_jsonl(llm_jsonl)

        disambig_rows = [
            row for row in rows if row.get("model_role") == "disambiguate_intent"
        ]
        assert len(disambig_rows) == 1, rows
        assert disambig_rows[0]["caller"] == "planner", disambig_rows[0]


if __name__ == "__main__":
    unittest.main()
