"""Runtime renders ``new_case_onboarding`` chat block verbatim.

The HITL handler in ``runtime.py`` wraps every other escalation with
:func:`format_hitl_prompt`, which adds a structured "Memory escalation
at ...: human input required." header. For onboarding we don't want
that wrapper — the chat block already carries the Y/n/customize call
to action and the recommendation list. This test pins the contract.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agentic_swmm.agent.memory_informed_policy import MemoryHITLRequired
from agentic_swmm.agent.runtime import run_openai_plan


class _StubPlanner:
    def __init__(self, escalation: MemoryHITLRequired) -> None:
        self._escalation = escalation

    def run(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - shape
        raise self._escalation


class _StubExecutor:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.results: list[dict[str, Any]] = []

    def close(self) -> None:
        return


class _StubRegistry:
    def sorted_names(self) -> list[str]:
        return []


class _StubProvider:
    pass


class RuntimeOnboardingRenderTests(unittest.TestCase):
    def test_onboarding_chat_block_rendered_verbatim(self) -> None:
        chat_block = (
            'Starting new case "vancouver". I have lessons from 1 similar '
            "past case(s):\n"
            "  • saanich (similarity 0.81, calibrated nse=0.750)\n"
            "Recommended starter calibration: parameters from saanich.\n"
            "\n"
            "Proceed with these defaults? [Y/n/customize]"
        )
        escalation = MemoryHITLRequired(
            chat_block,
            decision_point="new_case_onboarding",
            proposed_action="apply_transfer_learning_defaults",
        )

        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            session_dir.mkdir()
            executor = _StubExecutor(session_dir)
            trace_path = session_dir / "agent_trace.jsonl"

            # Monkey-patch OpenAIPlanner via dependency injection through
            # the module's globals — runtime constructs it internally
            # with provider+registry, so we shim via patching.
            from agentic_swmm.agent import runtime as runtime_mod

            original_planner = runtime_mod.OpenAIPlanner
            try:
                runtime_mod.OpenAIPlanner = lambda *args, **kwargs: _StubPlanner(  # type: ignore[assignment]
                    escalation
                )
                outcome = run_openai_plan(
                    goal="calibrate vancouver",
                    model="stub",
                    provider=_StubProvider(),
                    registry=_StubRegistry(),
                    executor=executor,
                    max_steps=1,
                    trace_path=trace_path,
                    verbose=False,
                    emit=lambda _m: None,
                )
            finally:
                runtime_mod.OpenAIPlanner = original_planner

        self.assertFalse(outcome.ok)
        # The runtime must render the chat block verbatim; the
        # structured header from ``format_hitl_prompt`` is NOT in
        # ``final_text``.
        self.assertEqual(chat_block, outcome.final_text)
        self.assertNotIn(
            "Memory escalation at new_case_onboarding",
            outcome.final_text,
        )

    def test_other_decision_points_still_wrap(self) -> None:
        """A non-onboarding escalation still goes through format_hitl_prompt."""
        escalation = MemoryHITLRequired(
            "ambiguous case name",
            decision_point="planner_intent_disambiguation",
            proposed_action="resolve_target_case",
        )

        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            session_dir.mkdir()
            executor = _StubExecutor(session_dir)
            trace_path = session_dir / "agent_trace.jsonl"

            from agentic_swmm.agent import runtime as runtime_mod

            original_planner = runtime_mod.OpenAIPlanner
            try:
                runtime_mod.OpenAIPlanner = lambda *a, **k: _StubPlanner(  # type: ignore[assignment]
                    escalation
                )
                outcome = run_openai_plan(
                    goal="run for saanich",
                    model="stub",
                    provider=_StubProvider(),
                    registry=_StubRegistry(),
                    executor=executor,
                    max_steps=1,
                    trace_path=trace_path,
                    verbose=False,
                    emit=lambda _m: None,
                )
            finally:
                runtime_mod.OpenAIPlanner = original_planner

        self.assertFalse(outcome.ok)
        self.assertIn(
            "Memory escalation at planner_intent_disambiguation",
            outcome.final_text,
        )


if __name__ == "__main__":
    unittest.main()
