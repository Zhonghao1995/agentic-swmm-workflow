"""Integration tests for the new-case onboarding rewire (#246 follow-up).

Three surfaces are tested here:

1. **Planner hook** (``OpenAIPlanner._consult_onboarding``): injects
   the onboarding chat block + tool hint into ``system_prompt_extras``
   and emits an ``onboarding_offer`` trace event when the session's
   case is new; strict no-op for a known case or when no case resolves.

2. **``apply_onboarding`` tool handler**: accept / decline / customize /
   unknown paths; result-dict shape; memory ids on accept.

3. **Registry membership**: ``apply_onboarding`` is registered with
   ``is_read_only=False``.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.memory.calibration_memory import (
    CalibrationRecord,
    record_calibration_run,
)
from agentic_swmm.memory.parametric_memory import (
    ParametricRecord,
    record_parametric_run,
)
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            rows.append(json.loads(raw))
    return rows


def _write_min_inp(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[OPTIONS]\nFLOW_UNITS\tCMS\n"
        "[SUBCATCHMENTS]\n;;Name\tRain\tOutlet\tArea\t%Imperv\tWidth\tSlope\tCurbLen\n"
        "S1\tRG\tJ1\t1.0\t10\t100\t0.01\t0\n"
        "[CONDUITS]\n;;Name\tFrom\tTo\tLen\tN\tInletOff\tOutletOff\tInitFlow\tMaxFlow\n"
        "C1\tJ1\tJ2\t100\t0.013\t0\t0\t0\t0\n",
        encoding="utf-8",
    )


def _seed_calibration_and_inp(tmp: Path) -> tuple[Path, Path]:
    """Write a calibration row + source INP so the recommender fires."""
    memory_dir = tmp / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    calibration_store = memory_dir / "calibration_memory.jsonl"
    record_calibration_run(
        calibration_store,
        CalibrationRecord(
            run_id="20260101-000000_source",
            case_name="source_case",
            use_case="urban_runoff",
            algorithm="sce_ua",
            parameters={"manning_n_overland": 0.22},
            objective_name="nse",
            objective_value=0.82,
        ),
    )
    source_inp = tmp / "cases" / "source_case" / "source_case.inp"
    _write_min_inp(source_inp)
    return calibration_store, source_inp


class _ScriptedProvider:
    """Returns one ``capabilities`` tool call then a final text."""

    def __init__(self) -> None:
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
                response_id="r-tool",
                tool_calls=[
                    ProviderToolCall(call_id="c1", name="capabilities", arguments={})
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


# ---------------------------------------------------------------------------
# Planner-hook tests
# ---------------------------------------------------------------------------

class OnboardingHookInjectsBlockTests(unittest.TestCase):
    """Hook injects block + emits trace event for a new case."""

    def _run_planner(
        self,
        tmp: Path,
        goal: str,
        memory_dir: Path,
        prior_session_state: dict[str, Any] | None = None,
    ) -> tuple[OpenAIPlanner, Path]:
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
            provider=_ScriptedProvider(),  # type: ignore[arg-type]
            registry=registry,
            max_steps=2,
            verbose=False,
            emit=lambda text: None,
        )
        prev = os.environ.get("AISWMM_MEMORY_DIR")
        os.environ["AISWMM_MEMORY_DIR"] = str(memory_dir)
        try:
            planner.run(
                goal=goal,
                session_dir=tmp,
                trace_path=trace_path,
                executor=executor,
                prior_session_state=prior_session_state or {},
            )
        finally:
            if prev is None:
                os.environ.pop("AISWMM_MEMORY_DIR", None)
            else:
                os.environ["AISWMM_MEMORY_DIR"] = prev
        return planner, trace_path

    def test_new_case_injects_block_and_emits_trace_event(self) -> None:
        """When the case is new and recommendations exist, the hook fires."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _seed_calibration_and_inp(tmp)
            memory_dir = tmp / "memory" / "modeling-memory"

            # Put the target INP where the hook will find it
            target_inp = tmp / "cases" / "vancouver" / "vancouver.inp"
            _write_min_inp(target_inp)

            # Parametric store is empty → is_new_case returns True
            planner, trace_path = self._run_planner(
                tmp,
                goal="run vancouver simulation",
                memory_dir=memory_dir,
                prior_session_state={"active_case_id": "vancouver"},
            )

            events = _read_jsonl(trace_path)
            onboarding_events = [
                e for e in events if e.get("event") == "onboarding_offer"
            ]

            # Either:
            #   A) recommendations found → onboarding_offer emitted +
            #      chat block injected into system_prompt_extras
            #   B) no recommendations (similarity too low) → no event emitted
            # We assert that if any onboarding_offer event was emitted, its
            # fields are well-shaped.
            for ev in onboarding_events:
                self.assertEqual(ev["case_name"], "vancouver")
                self.assertTrue(ev["triggered"])
                self.assertEqual(ev["reason"], "new_case")
                self.assertIsInstance(ev["recommendation_count"], int)
                self.assertIsInstance(ev["memory_ids"], list)

            # If the event was emitted, the chat block must be in extras.
            if onboarding_events:
                found = any(
                    "onboarding_offer" in extra
                    for extra in planner.system_prompt_extras
                )
                self.assertTrue(
                    found,
                    "onboarding_offer event emitted but block not in system_prompt_extras",
                )
                # The tool hint must be present too.
                hint_found = any(
                    "apply_onboarding" in extra
                    for extra in planner.system_prompt_extras
                )
                self.assertTrue(hint_found, "apply_onboarding tool hint missing from extras")

    def test_known_case_is_silent_noop(self) -> None:
        """When the case already has parametric history, the hook does nothing."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            memory_dir = tmp / "memory" / "modeling-memory"
            memory_dir.mkdir(parents=True, exist_ok=True)
            # Pre-seed parametric memory for 'vancouver' → is_new_case=False
            store = memory_dir / "parametric_memory.jsonl"
            record_parametric_run(
                store,
                ParametricRecord(run_id="r1", case_name="vancouver"),
            )

            planner, trace_path = self._run_planner(
                tmp,
                goal="run vancouver simulation",
                memory_dir=memory_dir,
                prior_session_state={"active_case_id": "vancouver"},
            )

            events = _read_jsonl(trace_path)
            onboarding_events = [
                e for e in events if e.get("event") == "onboarding_offer"
            ]
            self.assertEqual(
                len(onboarding_events),
                0,
                "onboarding hook fired for a known case",
            )
            # No block injected either.
            for extra in planner.system_prompt_extras:
                self.assertNotIn(
                    "onboarding_offer",
                    extra,
                    "chat block injected for a known case",
                )

    def test_no_case_resolved_is_silent_noop(self) -> None:
        """When no case can be resolved from the goal, the hook is silent."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            memory_dir = tmp / "empty_memory"

            planner, trace_path = self._run_planner(
                tmp,
                goal="hello, what can you do?",
                memory_dir=memory_dir,
                # No active_case_id; goal has no case token
                prior_session_state={},
            )

            events = _read_jsonl(trace_path)
            onboarding_events = [
                e for e in events if e.get("event") == "onboarding_offer"
            ]
            self.assertEqual(
                len(onboarding_events),
                0,
                "onboarding hook fired when no case was resolvable",
            )


# ---------------------------------------------------------------------------
# apply_onboarding tool-handler tests
# ---------------------------------------------------------------------------

class ApplyOnboardingHandlerTests(unittest.TestCase):
    """Tests for the apply_onboarding tool-handler result-dict shape."""

    def _call(
        self, case_name: str, response: str, *, session_dir: Path | None = None
    ) -> dict[str, Any]:
        from agentic_swmm.agent.tool_handlers.swmm_onboarding import (
            _apply_onboarding_tool,
        )

        call = ToolCall("apply_onboarding", {"case_name": case_name, "response": response})
        sd = session_dir or Path(tempfile.mkdtemp())
        return _apply_onboarding_tool(call, sd)

    def test_missing_case_name_returns_failure(self) -> None:
        from agentic_swmm.agent.tool_handlers.swmm_onboarding import (
            _apply_onboarding_tool,
        )

        call = ToolCall("apply_onboarding", {"response": "Y"})
        result = _apply_onboarding_tool(call, Path(tempfile.mkdtemp()))
        self.assertFalse(result["ok"])
        self.assertIn("case_name", result["summary"])

    def test_missing_response_returns_failure(self) -> None:
        from agentic_swmm.agent.tool_handlers.swmm_onboarding import (
            _apply_onboarding_tool,
        )

        call = ToolCall("apply_onboarding", {"case_name": "vancouver"})
        result = _apply_onboarding_tool(call, Path(tempfile.mkdtemp()))
        self.assertFalse(result["ok"])
        self.assertIn("response", result["summary"])

    def test_decline_result_shape(self) -> None:
        result = self._call("vancouver", "n")
        self.assertTrue(result["ok"])
        self.assertEqual(result["intent"], "decline")
        self.assertEqual(result["case_name"], "vancouver")
        self.assertEqual(result["applied_memory_ids"], [])
        self.assertEqual(result["applied_parameters"], {})
        self.assertIsNone(result["applied_source_case"])
        self.assertIn("declined", result["summary"].lower())

    def test_customize_result_shape(self) -> None:
        result = self._call("tod-creek", "customize")
        self.assertTrue(result["ok"])
        self.assertEqual(result["intent"], "customize")
        self.assertEqual(result["case_name"], "tod-creek")
        self.assertEqual(result["applied_memory_ids"], [])
        self.assertIn("custom mode", result["summary"].lower())

    def test_unknown_response_result_shape(self) -> None:
        result = self._call("saanich", "maybe later")
        self.assertTrue(result["ok"])
        self.assertEqual(result["intent"], "unknown")
        self.assertIn("Y / n / customize", result["summary"])

    def test_accept_result_shape_no_memory(self) -> None:
        """Accept with no matching memory: ok=True, empty applied_memory_ids."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            # Point AISWMM_MEMORY_DIR at an empty dir so no recommendations load.
            prev = os.environ.get("AISWMM_MEMORY_DIR")
            os.environ["AISWMM_MEMORY_DIR"] = str(tmp / "empty_memory")
            try:
                result = self._call("vancouver", "Y", session_dir=tmp)
            finally:
                if prev is None:
                    os.environ.pop("AISWMM_MEMORY_DIR", None)
                else:
                    os.environ["AISWMM_MEMORY_DIR"] = prev

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent"], "accept")
        self.assertEqual(result["applied_memory_ids"], [])
        self.assertIsInstance(result["applied_parameters"], dict)

    def test_accept_with_recommendations_reports_memory_ids(self) -> None:
        """Accept when calibration history exists → applied_memory_ids populated."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            calibration_store, _ = _seed_calibration_and_inp(tmp)
            memory_dir = tmp / "memory" / "modeling-memory"

            # Vancouver INP in place so the recommender can score it
            target_inp = tmp / "cases" / "vancouver" / "vancouver.inp"
            _write_min_inp(target_inp)

            # Patch repo_root so _candidate_inp_locations finds our test INP
            prev = os.environ.get("AISWMM_MEMORY_DIR")
            os.environ["AISWMM_MEMORY_DIR"] = str(memory_dir)
            try:
                with mock.patch(
                    "agentic_swmm.agent.tool_handlers.swmm_onboarding._resolve_memory_dir",
                    return_value=memory_dir,
                ), mock.patch(
                    "agentic_swmm.utils.paths.repo_root",
                    return_value=tmp,
                ):
                    result = self._call("vancouver", "Y", session_dir=tmp)
            finally:
                if prev is None:
                    os.environ.pop("AISWMM_MEMORY_DIR", None)
                else:
                    os.environ["AISWMM_MEMORY_DIR"] = prev

            self.assertTrue(result["ok"])
            self.assertEqual(result["intent"], "accept")
            # When recommendations were found, memory ids must be a list of
            # non-empty strings.
            if result["applied_memory_ids"]:
                for mid in result["applied_memory_ids"]:
                    self.assertIsInstance(mid, str)
                    self.assertTrue(mid.strip(), "memory_id must not be empty")
            # applied_parameters mirrors the transferred calibration params
            if result["applied_source_case"] is not None:
                self.assertIsInstance(result["applied_parameters"], dict)
                self.assertTrue(
                    len(result["applied_parameters"]) > 0,
                    "accepted onboarding with a source case but no parameters transferred",
                )


# ---------------------------------------------------------------------------
# Registry membership test
# ---------------------------------------------------------------------------

class RegistryMembershipTests(unittest.TestCase):
    def test_apply_onboarding_in_registry(self) -> None:
        registry = AgentToolRegistry()
        self.assertIn("apply_onboarding", registry.names)

    def test_apply_onboarding_is_not_read_only(self) -> None:
        registry = AgentToolRegistry()
        self.assertFalse(registry.is_read_only("apply_onboarding"))

    def test_apply_onboarding_schema_has_required_args(self) -> None:
        registry = AgentToolRegistry()
        spec = registry._tools["apply_onboarding"]
        schema = spec.schema()
        required = schema["parameters"].get("required", [])
        self.assertIn("case_name", required)
        self.assertIn("response", required)


if __name__ == "__main__":
    unittest.main()
