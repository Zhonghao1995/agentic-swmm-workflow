"""Runtime + HITL formatter integration (PRD-06 Phase D.2).

The planner raises ``MemoryHITLRequired`` with a populated
``memory_context``; the runtime catches it and renders the structured
prompt via :func:`format_hitl_prompt`. This test pins the
end-to-end path so a future refactor cannot silently drop the
formatter and fall back to the bare exception message.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.memory_context import MemoryContext, ParametricRecord
from agentic_swmm.agent.memory_informed_policy import MemoryHITLRequired
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.runtime import run_openai_plan
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.providers.base import ProviderToolResponse


class _RaisingProvider:
    """Provider stub that triggers the planner's policy hook.

    Not actually called — the planner's
    ``_consult_memory_informed_policy`` fires before any provider
    round-trip when the goal text is a recognised SWMM request.
    """

    def respond_with_tools(
        self, **kwargs: Any
    ) -> ProviderToolResponse:  # pragma: no cover - not exercised
        return ProviderToolResponse(
            text="should not be reached",
            model="stub",
            response_id="r1",
            tool_calls=[],
            raw={},
        )


class _RaisingPlanner:
    """Drop-in for ``Planner`` that raises ``MemoryHITLRequired``.

    We exercise the runtime's catch block without booting the real
    planner. The exception carries a populated MemoryContext so the
    formatter has something to render.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def run(self, **kwargs: Any) -> Any:
        ctx = MemoryContext(
            parametric_hits=[
                ParametricRecord(
                    run_id="run-1",
                    case_name="saanich-b8",
                    qa_metrics={"runoff_continuity_pct": 0.8},
                    recorded_utc="2026-05-15T09:30:00Z",
                ),
            ],
            summary="1 prior run of saanich-b8, mean runoff continuity 0.80%.",
        )
        raise MemoryHITLRequired(
            "high-stakes action requested but memory has zero matching parametric records",
            memory_context=ctx,
            proposed_action="accept-calibration for saanich-b8",
            decision_point="planner_intent_disambiguation",
        )


class RuntimeHitlIntegrationTests(unittest.TestCase):
    """Pin the runtime's catch block renders via the formatter."""

    def test_hitl_final_text_is_formatted_prompt(self) -> None:
        from agentic_swmm.agent import runtime as runtime_mod

        # Patch the planner constructor in runtime to our raiser.
        original_planner = runtime_mod.Planner
        runtime_mod.Planner = _RaisingPlanner  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                session_dir = Path(tmp)
                trace_path = session_dir / "agent_trace.jsonl"
                registry = AgentToolRegistry()
                executor = AgentExecutor(
                    registry,
                    session_dir=session_dir,
                    trace_path=trace_path,
                    dry_run=True,
                    profile=Profile.QUICK,
                )

                outcome = run_openai_plan(
                    goal="accept calibration for saanich",
                    model="stub",
                    provider=_RaisingProvider(),
                    registry=registry,
                    executor=executor,
                    max_steps=1,
                    trace_path=trace_path,
                    verbose=False,
                    emit=lambda text: None,
                )

                # The escalation message must appear in the final text.
                self.assertIn(
                    "high-stakes action requested",
                    outcome.final_text,
                )
                # The proposed action surfaced from the exception.
                self.assertIn(
                    "accept-calibration for saanich-b8",
                    outcome.final_text,
                )
                # The closing question is always there.
                # PRD-08 A.3: action vocabulary replaces the bare
                # "Please confirm or override." line.
                self.assertIn(
                    "Please respond:",
                    outcome.final_text,
                )
                # Memory summary surfaces too.
                self.assertIn("saanich-b8", outcome.final_text)
                # The agent should NOT be marked ok on a hitl.
                self.assertFalse(outcome.ok)
        finally:
            runtime_mod.Planner = original_planner  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
