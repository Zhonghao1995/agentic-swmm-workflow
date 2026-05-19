"""``PreparedInpMode`` — run + audit + plot a user-supplied SWMM INP.

PRD-04. Migrated from ``OpenAIPlanner._run_prepared_inp_workflow``.
The class attributes are the spec ``select_workflow_mode`` reads; the
``run`` method drives the planner's tool dispatch for this mode.

Behavioural parity with the legacy private method is locked by
``tests/test_workflow_mode_adapter_run_parity.py`` plus the end-to-end
planner tests in ``tests/test_agentic_swmm_cli.py``.
"""

from __future__ import annotations

from agentic_swmm.agent.types import ToolCall
from agentic_swmm.agent.workflow_modes._helpers import (
    extract_after_label,
    extract_plot_choice,
    plot_choice_prompt,
    plot_output_path,
    prepared_inp_done_text,
    workflow_route_args,
)
from agentic_swmm.agent.workflow_modes.base import WorkflowContext, register


@register
class PreparedInpMode:
    name = "prepared_inp_cli"
    required_inputs = ["inp_path"]
    recommended_next_tools = [
        "run_swmm_inp",
        "audit_run",
        "inspect_plot_options",
        "plot_run",
    ]
    evidence_boundary = (
        "Prepared INP execution is runnable/checkable/auditable evidence, "
        "not calibration or validation by itself."
    )

    def run(self, ctx: WorkflowContext):
        # Late import avoids the planner -> workflow_modes import cycle.
        from agentic_swmm.agent.planner import PlannerRun

        inp_path = str(
            ctx.route.get("provided_values", {}).get("inp_path")
            or workflow_route_args(ctx.goal).get("inp_path")
            or ""
        )
        if not inp_path:
            return PlannerRun(
                ok=True,
                plan=ctx.plan,
                results=ctx.executor.results,
                final_text="Please provide a SWMM INP path before running.",
            )

        node = extract_after_label(ctx.goal, ("node", "outfall", "节点", "出口"))
        run_args = {
            "inp_path": inp_path,
            "run_id": ctx.session_dir.name,
            "run_dir": str(ctx.session_dir),
        }
        if node:
            run_args["node"] = node
        run_result = ctx.step(ToolCall("run_swmm_inp", run_args))
        if not run_result.get("ok"):
            return PlannerRun(
                ok=False,
                plan=ctx.plan,
                results=ctx.executor.results,
                final_text="SWMM run failed; inspect the saved stderr/stdout artifacts.",
            )

        audit_result = ctx.step(
            ToolCall(
                "audit_run",
                {
                    "run_dir": str(ctx.session_dir),
                    "workflow_mode": "prepared_inp_cli",
                    "objective": ctx.goal,
                },
            )
        )
        if not audit_result.get("ok"):
            return PlannerRun(
                ok=False,
                plan=ctx.plan,
                results=ctx.executor.results,
                final_text="SWMM ran, but audit generation failed; inspect the saved audit tool artifacts.",
            )

        options_result = ctx.step(
            ToolCall("inspect_plot_options", {"run_dir": str(ctx.session_dir)})
        )
        if not options_result.get("ok"):
            return PlannerRun(
                ok=False,
                plan=ctx.plan,
                results=ctx.executor.results,
                final_text="SWMM ran and audit passed, but plot option inspection failed.",
            )

        options = options_result.get("results") if isinstance(options_result.get("results"), dict) else {}
        plot_choice = extract_plot_choice(ctx.goal, options)
        if plot_choice is None:
            return PlannerRun(
                ok=True,
                plan=ctx.plan,
                results=ctx.executor.results,
                final_text=plot_choice_prompt(ctx.session_dir, options),
            )

        plot_path = plot_output_path(ctx.session_dir, plot_choice)
        plot_args = {"run_dir": str(ctx.session_dir), **plot_choice, "out_png": str(plot_path)}
        plot_result = ctx.step(ToolCall("plot_run", plot_args))
        if not plot_result.get("ok"):
            return PlannerRun(
                ok=False,
                plan=ctx.plan,
                results=ctx.executor.results,
                final_text="SWMM ran and audit passed, but plot generation failed.",
            )
        return PlannerRun(
            ok=True,
            plan=ctx.plan,
            results=ctx.executor.results,
            final_text=prepared_inp_done_text(ctx.session_dir, plot_path=plot_path),
        )
