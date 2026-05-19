"""Adapter ``run`` parity (PRD-04 cycles 10-12).

The migration moves each ``OpenAIPlanner._run_X_workflow`` body into a
class method on the matching ``WorkflowMode`` adapter. The planner's
dispatch becomes a registry lookup.

Parity contract: calling the adapter's ``run`` with a faithfully
constructed ``WorkflowContext`` must produce the same ``PlannerRun`` —
identical plan, identical final_text, identical ok — as the legacy
private method on the planner. These tests pin that contract before
the planner dispatch is rewired so a behavioural delta would fail the
adapter test, not the integration test that's three layers up.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agentic_swmm.agent.types import ToolCall


class _FakePreparedInpExecutor:
    """Minimal executor that records calls and returns canned results.

    Mirrors the contract of ``AgentExecutor.execute`` used by the
    planner workflow adapters: returns dict with ``ok`` and a
    ``results`` payload that the adapter inspects.
    """

    def __init__(self, *, run_dir: str | None = None) -> None:
        self.results: list[dict[str, Any]] = []
        self.dry_run = False
        self.run_dir = run_dir or ""
        self.recorded: list[ToolCall] = []

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        self.recorded.append(call)
        name = call.name
        if name == "run_swmm_inp":
            result = {"tool": name, "args": call.args, "ok": True, "summary": "run completed"}
        elif name == "audit_run":
            result = {"tool": name, "args": call.args, "ok": True, "summary": "audit completed"}
        elif name == "inspect_plot_options":
            result = {
                "tool": name,
                "args": call.args,
                "ok": True,
                "summary": "options",
                "results": {
                    "rainfall_options": [{"name": "RAIN_A", "used_by_raingage": True}],
                    "node_options": ["J1", "OU2"],
                    "node_attribute_options": [
                        {"name": "Depth_above_invert", "label": "depth"},
                        {"name": "Total_inflow", "label": "flow"},
                    ],
                    "defaults": {
                        "node": "J1",
                        "node_attr": "Total_inflow",
                        "rain_ts": "RAIN_A",
                    },
                    "selections_needed": [],
                    "user_prompt": "",
                },
            }
        elif name == "plot_run":
            result = {"tool": name, "args": call.args, "ok": True, "summary": "plot saved"}
        else:  # pragma: no cover - sentinel
            result = {"tool": name, "args": call.args, "ok": True, "summary": "ok"}
        self.results.append(result)
        return result


def _build_planner() -> Any:
    from agentic_swmm.agent.planner import OpenAIPlanner
    from agentic_swmm.agent.tool_registry import AgentToolRegistry

    return OpenAIPlanner(
        provider=None,  # type: ignore[arg-type]
        registry=AgentToolRegistry(),
        max_steps=8,
        verbose=False,
        emit=lambda text: None,
    )


class PreparedInpModeRunParityTests(unittest.TestCase):
    """``PreparedInpMode.run`` must match ``_run_prepared_inp_workflow``."""

    def test_run_swmm_plus_audit_plus_plot_when_user_picks_depth(self) -> None:
        from agentic_swmm.agent.planner import PlannerRun
        from agentic_swmm.agent.workflow_modes import WorkflowContext, get_mode

        adapter = get_mode("prepared_inp_cli")
        self.assertIsNotNone(adapter)

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            plan: list[ToolCall] = []
            executor = _FakePreparedInpExecutor()
            ctx = WorkflowContext(
                goal="examples/tecnopolo/ 帮我画 OU2 的水深图",
                session_dir=session_dir,
                plan=plan,
                route={
                    "mode": "prepared_inp_cli",
                    "provided_values": {
                        "inp_path": "examples/tecnopolo/tecnopolo_r1_199401.inp"
                    },
                },
                executor=executor,  # type: ignore[arg-type]
                emit=lambda text: None,
            )
            outcome = adapter.run(ctx)

        self.assertIsInstance(outcome, PlannerRun)
        self.assertTrue(outcome.ok)
        plan_names = [call.name for call in plan]
        self.assertEqual(
            plan_names,
            ["run_swmm_inp", "audit_run", "inspect_plot_options", "plot_run"],
        )
        self.assertEqual(plan[-1].args["node"], "OU2")
        self.assertEqual(plan[-1].args["node_attr"], "Depth_above_invert")

    def test_stops_to_prompt_when_no_plot_choice_extractable(self) -> None:
        from agentic_swmm.agent.workflow_modes import WorkflowContext, get_mode

        adapter = get_mode("prepared_inp_cli")
        assert adapter is not None

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            plan: list[ToolCall] = []
            executor = _FakePreparedInpExecutor()
            ctx = WorkflowContext(
                goal="examples/tecnopolo/。你帮我跑一下这个我看看",
                session_dir=session_dir,
                plan=plan,
                route={
                    "mode": "prepared_inp_cli",
                    "provided_values": {
                        "inp_path": "examples/tecnopolo/tecnopolo_r1_199401.inp"
                    },
                },
                executor=executor,  # type: ignore[arg-type]
                emit=lambda text: None,
            )
            outcome = adapter.run(ctx)

        self.assertTrue(outcome.ok)
        plan_names = [call.name for call in plan]
        self.assertEqual(plan_names, ["run_swmm_inp", "audit_run", "inspect_plot_options"])
        self.assertIn("Before plotting", outcome.final_text)
        self.assertIn("Depth_above_invert", outcome.final_text)

    def test_returns_missing_inp_prompt_when_inp_path_absent(self) -> None:
        from agentic_swmm.agent.workflow_modes import WorkflowContext, get_mode

        adapter = get_mode("prepared_inp_cli")
        assert adapter is not None

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            plan: list[ToolCall] = []
            executor = _FakePreparedInpExecutor()
            ctx = WorkflowContext(
                goal="please run something",
                session_dir=session_dir,
                plan=plan,
                route={"mode": "prepared_inp_cli", "provided_values": {}},
                executor=executor,  # type: ignore[arg-type]
                emit=lambda text: None,
            )
            outcome = adapter.run(ctx)

        self.assertTrue(outcome.ok)
        self.assertEqual(plan, [])
        self.assertIn("SWMM INP path", outcome.final_text)


class ExistingRunPlotModeRunParityTests(unittest.TestCase):
    def test_plots_when_user_picks_node_and_variable(self) -> None:
        from agentic_swmm.agent.workflow_modes import WorkflowContext, get_mode

        adapter = get_mode("existing_run_plot")
        assert adapter is not None

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            plan: list[ToolCall] = []
            executor = _FakePreparedInpExecutor()
            ctx = WorkflowContext(
                goal="换成 node J1 的水深图",
                session_dir=session_dir,
                plan=plan,
                route={
                    "mode": "existing_run_plot",
                    "provided_values": {"run_dir": "runs/foo"},
                },
                executor=executor,  # type: ignore[arg-type]
                emit=lambda text: None,
            )
            outcome = adapter.run(ctx)

        self.assertTrue(outcome.ok)
        plan_names = [call.name for call in plan]
        self.assertEqual(plan_names, ["inspect_plot_options", "plot_run"])
        self.assertEqual(plan[-1].args["node"], "J1")
        self.assertEqual(plan[-1].args["node_attr"], "Depth_above_invert")


class AuditOnlyOrComparisonModeRunParityTests(unittest.TestCase):
    def test_calls_audit_run_with_supplied_run_dir(self) -> None:
        from agentic_swmm.agent.workflow_modes import WorkflowContext, get_mode

        adapter = get_mode("audit_only_or_comparison")
        assert adapter is not None

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            plan: list[ToolCall] = []
            executor = _FakePreparedInpExecutor()
            ctx = WorkflowContext(
                goal="audit it",
                session_dir=session_dir,
                plan=plan,
                route={
                    "mode": "audit_only_or_comparison",
                    "provided_values": {"run_dir": "runs/2026-05-11/foo"},
                },
                executor=executor,  # type: ignore[arg-type]
                emit=lambda text: None,
            )
            outcome = adapter.run(ctx)

        self.assertTrue(outcome.ok)
        self.assertEqual([call.name for call in plan], ["audit_run"])
        self.assertEqual(plan[0].args["run_dir"], "runs/2026-05-11/foo")
        self.assertIn("audit", outcome.final_text.lower())

    def test_returns_prompt_when_no_run_dir(self) -> None:
        from agentic_swmm.agent.workflow_modes import WorkflowContext, get_mode

        adapter = get_mode("audit_only_or_comparison")
        assert adapter is not None

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            plan: list[ToolCall] = []
            executor = _FakePreparedInpExecutor()
            ctx = WorkflowContext(
                goal="audit it",
                session_dir=session_dir,
                plan=plan,
                route={"mode": "audit_only_or_comparison", "provided_values": {}},
                executor=executor,  # type: ignore[arg-type]
                emit=lambda text: None,
            )
            outcome = adapter.run(ctx)

        self.assertTrue(outcome.ok)
        self.assertEqual(plan, [])
        self.assertIn("run directory", outcome.final_text.lower())
