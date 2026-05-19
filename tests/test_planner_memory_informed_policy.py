"""Integration tests for the planner's memory-informed policy hook (PRD-07 Phase 3).

The planner consults the memory-informed policy *before* the existing
LLM disambiguator. These tests pin three integration-level guarantees:

1. **No regression on no-memory codepath** — with no parametric memory
   present, the planner runs exactly as before (existing
   disambiguator audit-trail tests already cover the LLM path).
2. **A saved parametric_memory.jsonl fires the auto_complete branch**
   — when the case is resolvable and memory has one matching row, the
   ``memory_informed_policy`` trace event is recorded with
   ``confidence="auto_complete"``.
3. **One memory_trace.jsonl line per disambiguation decision** — the
   audit trail must capture every consultation regardless of outcome.

These tests exercise ``OpenAIPlanner`` end-to-end with a scripted
provider so they run in milliseconds and stay deterministic.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.memory_informed_policy import MemoryHITLRequired
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.memory.parametric_memory import (
    ParametricRecord as StoredParametricRecord,
    record_parametric_run,
)
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


class _ScriptedProvider:
    """Echoes a fake-tool ``classify_workflow_mode`` then a final text.

    Mirrors the existing audit-trail test fixture so we don't take a
    dependency on a real provider while exercising the planner's
    disambiguation surface.
    """

    def __init__(self, picked_mode: str = "prepared_demo") -> None:
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
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            rows.append(json.loads(raw))
    return rows


def _seed_parametric_memory(memory_dir: Path, **overrides: object) -> None:
    """Write one parametric_memory.jsonl row into ``memory_dir``."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    defaults: dict[str, object] = {
        "run_id": "run-x",
        "case_name": "saanich-b8",
        "swmm_version": "5.2.4",
        "model_structure": {"use_case": "stormwater_event"},
        "qa_metrics": {"runoff_continuity_pct": 0.5},
        "performance_metrics": {},
        "watershed_classification": {},
    }
    defaults.update(overrides)
    record = StoredParametricRecord(**defaults)  # type: ignore[arg-type]
    record_parametric_run(memory_dir / "parametric_memory.jsonl", record)


class _PlannerHarness:
    """Build + run an ``OpenAIPlanner`` against a temp session_dir.

    Used as a contextmanager so we can seed env vars (``AISWMM_MEMORY_DIR``)
    around the planner.run call and restore them afterwards.
    """

    def __init__(
        self,
        tmp: Path,
        goal: str,
        *,
        memory_dir: Path | None = None,
        picked_mode: str = "prepared_demo",
        prior_session_state: dict[str, Any] | None = None,
    ) -> None:
        self.tmp = tmp
        self.goal = goal
        self.memory_dir = memory_dir
        self.picked_mode = picked_mode
        self.prior_session_state = prior_session_state or {}

    def run(self) -> tuple[Path, Path]:
        trace_path = self.tmp / "agent_trace.jsonl"
        memory_trace_path = self.tmp / "memory_trace.jsonl"
        registry = AgentToolRegistry()
        executor = AgentExecutor(
            registry,
            session_dir=self.tmp,
            trace_path=trace_path,
            dry_run=False,
            profile=Profile.QUICK,
        )
        planner = OpenAIPlanner(
            provider=_ScriptedProvider(self.picked_mode),  # type: ignore[arg-type]
            registry=registry,
            max_steps=2,
            verbose=False,
            emit=lambda text: None,
        )
        prev = os.environ.get("AISWMM_MEMORY_DIR")
        if self.memory_dir is not None:
            os.environ["AISWMM_MEMORY_DIR"] = str(self.memory_dir)
        try:
            planner.run(
                goal=self.goal,
                session_dir=self.tmp,
                trace_path=trace_path,
                executor=executor,
                prior_session_state=self.prior_session_state,
            )
        finally:
            if prev is None:
                os.environ.pop("AISWMM_MEMORY_DIR", None)
            else:
                os.environ["AISWMM_MEMORY_DIR"] = prev
        return trace_path, memory_trace_path


class NoRegressionOnEmptyMemoryTests(unittest.TestCase):
    """Slice 1 — the planner must keep dispatching when memory is absent."""

    def test_plot_conflict_goal_still_records_intent_disambiguation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            # No memory_dir override: the planner reads
            # ``memory/modeling-memory`` (which doesn't exist under
            # tmp's CWD), MemoryContext is empty, policy yields
            # ``llm``, and the disambiguator runs normally.
            harness = _PlannerHarness(
                tmp,
                goal="run Tod Creek demo and plot the figure",
                memory_dir=tmp / "empty-memory",
                picked_mode="prepared_demo",
            )
            trace_path, _ = harness.run()
            events = _read_jsonl(trace_path)

        # The existing intent_disambiguation event still fires.
        intent_events = [
            e for e in events if e.get("event") == "intent_disambiguation"
        ]
        self.assertEqual(len(intent_events), 1, intent_events)
        self.assertEqual(intent_events[0]["picked_mode"], "prepared_demo")
        # The new memory_informed_policy event also fires and reports
        # the ``llm`` deferral.
        policy_events = [
            e for e in events if e.get("event") == "memory_informed_policy"
        ]
        self.assertEqual(len(policy_events), 1, policy_events)
        self.assertEqual(policy_events[0]["confidence"], "llm")


class AutoCompleteBranchFiresTests(unittest.TestCase):
    """Slice 2 — a saved parametric_memory row resolves to auto_complete."""

    def test_one_parametric_hit_yields_auto_complete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            memory_dir = tmp / "memory" / "modeling-memory"
            _seed_parametric_memory(
                memory_dir, run_id="r1", case_name="saanich-b8"
            )
            harness = _PlannerHarness(
                tmp,
                # The goal carries an explicit case-name token AND a
                # plot-conflict, so the existing disambiguator would
                # also fire. The memory hook precedes it.
                goal="run saanich-b8 demo and plot the figure",
                memory_dir=memory_dir,
                prior_session_state={"active_case_id": "saanich-b8"},
            )
            trace_path, memory_trace_path = harness.run()
            events = _read_jsonl(trace_path)
            memory_lines = _read_jsonl(memory_trace_path)

        policy_events = [
            e for e in events if e.get("event") == "memory_informed_policy"
        ]
        self.assertEqual(len(policy_events), 1, policy_events)
        self.assertEqual(policy_events[0]["confidence"], "auto_complete")
        self.assertEqual(policy_events[0]["resolved_case"], "saanich-b8")

        # memory_trace.jsonl must record the same decision.
        disambig_lines = [
            line
            for line in memory_lines
            if line.get("decision_point") == "planner_intent_disambiguation"
        ]
        self.assertEqual(len(disambig_lines), 1, disambig_lines)
        self.assertEqual(disambig_lines[0]["confidence"], "auto_complete")
        self.assertEqual(disambig_lines[0]["decision_taken"], "saanich-b8")


class MemoryTraceCoversEveryDecisionTests(unittest.TestCase):
    """Slice 3 — every consultation produces one memory_trace line."""

    def test_empty_memory_still_writes_memory_trace_entry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            harness = _PlannerHarness(
                tmp,
                goal="run Tod Creek demo and plot the figure",
                memory_dir=tmp / "empty-memory",
                picked_mode="prepared_demo",
            )
            _, memory_trace_path = harness.run()
            lines = _read_jsonl(memory_trace_path)

        disambig_lines = [
            line
            for line in lines
            if line.get("decision_point") == "planner_intent_disambiguation"
        ]
        self.assertEqual(len(disambig_lines), 1, disambig_lines)
        self.assertEqual(disambig_lines[0]["confidence"], "llm")
        # parametric_hit_count is 0 — and the line still landed.
        self.assertEqual(disambig_lines[0]["parametric_hit_count"], 0)


class RuntimeCatchesHITLTests(unittest.TestCase):
    """Slice 5 — ``run_openai_plan`` converts MemoryHITLRequired into a
    failed :class:`PlannerRun` carrying the escalation prompt as
    ``final_text``. The CLI surface never sees an uncaught exception.
    """

    def test_run_openai_plan_swallows_hitl_into_planner_run(self) -> None:
        from agentic_swmm.agent.runtime import run_openai_plan

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            trace_path = tmp / "agent_trace.jsonl"
            registry = AgentToolRegistry()
            executor = AgentExecutor(
                registry,
                session_dir=tmp,
                trace_path=trace_path,
                dry_run=False,
                profile=Profile.QUICK,
            )

            prev = os.environ.get("AISWMM_MEMORY_DIR")
            os.environ["AISWMM_MEMORY_DIR"] = str(tmp / "empty-memory")
            try:
                outcome = run_openai_plan(
                    goal=(
                        "accept-calibration for saanich-b8 "
                        "and plot the figure"
                    ),
                    model="stub",
                    provider=_ScriptedProvider("prepared_demo"),
                    registry=registry,
                    executor=executor,
                    max_steps=2,
                    trace_path=trace_path,
                    verbose=False,
                    emit=lambda text: None,
                    prior_session_state={"active_case_id": "saanich-b8"},
                )
            finally:
                if prev is None:
                    os.environ.pop("AISWMM_MEMORY_DIR", None)
                else:
                    os.environ["AISWMM_MEMORY_DIR"] = prev

            events = _read_jsonl(trace_path)

        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.final_text.strip())
        # ``run_openai_plan`` wrote a memory_hitl_required event so a
        # reviewer can find the escalation in the audit trail.
        hitl_events = [
            e for e in events if e.get("event") == "memory_hitl_required"
        ]
        self.assertEqual(len(hitl_events), 1, hitl_events)


class HighStakesEscalationIntegrationTests(unittest.TestCase):
    """Slice 4 — high-stakes goal with no memory raises MemoryHITLRequired."""

    def test_calibration_accept_with_empty_memory_raises_hitl(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
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
                provider=_ScriptedProvider("prepared_demo"),  # type: ignore[arg-type]
                registry=registry,
                max_steps=2,
                verbose=False,
                emit=lambda text: None,
            )

            prev = os.environ.get("AISWMM_MEMORY_DIR")
            os.environ["AISWMM_MEMORY_DIR"] = str(tmp / "empty-memory")
            try:
                with self.assertRaises(MemoryHITLRequired) as cm:
                    planner.run(
                        goal=(
                            "accept-calibration for saanich-b8 "
                            "and plot the figure"
                        ),
                        session_dir=tmp,
                        trace_path=trace_path,
                        executor=executor,
                        prior_session_state={"active_case_id": "saanich-b8"},
                    )
            finally:
                if prev is None:
                    os.environ.pop("AISWMM_MEMORY_DIR", None)
                else:
                    os.environ["AISWMM_MEMORY_DIR"] = prev

            # Read the trace inside the TemporaryDirectory context so
            # the files still exist on disk.
            self.assertTrue(str(cm.exception).strip())
            events = _read_jsonl(trace_path)

        policy_events = [
            e for e in events if e.get("event") == "memory_informed_policy"
        ]
        # The escalation event lands before the exception unwinds.
        self.assertEqual(len(policy_events), 1)
        self.assertEqual(policy_events[0]["confidence"], "hitl")
        self.assertEqual(policy_events[0]["stakes"], "high")


if __name__ == "__main__":
    unittest.main()
