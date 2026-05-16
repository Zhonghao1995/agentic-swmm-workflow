"""Planner auto-route wires the intent disambiguator (#111).

The planner's auto-route fast-path calls ``select_workflow_mode``
without first asking the LLM to classify intent. For compound
plot-conflict goals this hijacked the request (see PRD #111). The fix
injects a single LLM disambiguation call between the keyword-derived
signals and the tool dispatch, then forwards the disambiguator's mode
into the tool args as ``mode=<picked>`` so the tool's existing
explicit-mode short-circuit fires.

These tests pin the wiring: when the disambiguator returns a mode the
planner injects it; when it returns ``None`` the planner falls
through to the original behaviour.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.providers.base import ProviderToolResponse


class _FinalTextProvider:
    """Returns a final-text response immediately.

    After the auto-route fires the planner enters its OpenAI loop; we
    short-circuit with a no-tool-call response so the test exits
    cleanly without driving the full loop.
    """

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        return ProviderToolResponse(
            text="done",
            model="stub",
            response_id="final",
            tool_calls=[],
            raw={},
        )


class _Spying:
    """Wraps ``AgentExecutor.execute`` to capture every call."""

    def __init__(self, executor: AgentExecutor) -> None:
        self._executor = executor
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, call: Any, index: int | None = None) -> dict[str, Any]:
        self.calls.append((call.name, dict(call.args)))
        return self._executor.execute(call, index=index)

    @property
    def results(self) -> list[dict[str, Any]]:
        return self._executor.results

    @property
    def dry_run(self) -> bool:
        return self._executor.dry_run


class PlannerAutoRouteDisambiguatorWiringTests(unittest.TestCase):
    def _make_planner(self) -> OpenAIPlanner:
        return OpenAIPlanner(
            provider=_FinalTextProvider(),  # type: ignore[arg-type]
            registry=AgentToolRegistry(),
            max_steps=2,
            verbose=False,
            emit=lambda text: None,
        )

    def test_disambiguator_mode_is_injected_into_select_workflow_mode_args(self) -> None:
        """When the disambiguator returns ``prepared_demo`` for a
        compound goal, the planner must call ``select_workflow_mode``
        with ``mode="prepared_demo"`` so the tool's explicit-mode
        short-circuit fires (no keyword fallback)."""

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            registry = AgentToolRegistry()
            spy = _Spying(
                AgentExecutor(
                    registry,
                    session_dir=session_dir,
                    trace_path=trace_path,
                    dry_run=False,
                    profile=Profile.QUICK,
                )
            )
            planner = self._make_planner()

            with mock.patch(
                "agentic_swmm.agent.planner.disambiguate",
                return_value="prepared_demo",
            ):
                planner.run(
                    goal="run Tod Creek demo and plot the figure",
                    session_dir=session_dir,
                    trace_path=trace_path,
                    executor=spy,  # type: ignore[arg-type]
                )

        route_calls = [args for name, args in spy.calls if name == "select_workflow_mode"]
        assert route_calls, f"expected select_workflow_mode call; got {spy.calls}"
        assert route_calls[0].get("mode") == "prepared_demo", route_calls[0]

    def test_disambiguator_none_does_not_inject_mode(self) -> None:
        """When the disambiguator returns ``None`` (trigger doesn't fire,
        timeout, error, …) the planner must call the tool *without* a
        ``mode`` arg so the keyword fallback runs unchanged."""

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            registry = AgentToolRegistry()
            spy = _Spying(
                AgentExecutor(
                    registry,
                    session_dir=session_dir,
                    trace_path=trace_path,
                    dry_run=False,
                    profile=Profile.QUICK,
                )
            )
            planner = self._make_planner()

            with mock.patch(
                "agentic_swmm.agent.planner.disambiguate",
                return_value=None,
            ):
                planner.run(
                    goal="run the prepared INP at examples/foo.inp",
                    session_dir=session_dir,
                    trace_path=trace_path,
                    executor=spy,  # type: ignore[arg-type]
                )

        route_calls = [args for name, args in spy.calls if name == "select_workflow_mode"]
        assert route_calls, f"expected select_workflow_mode call; got {spy.calls}"
        assert "mode" not in route_calls[0], route_calls[0]


if __name__ == "__main__":
    unittest.main()
