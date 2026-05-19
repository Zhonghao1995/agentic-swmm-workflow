"""``ExistingRunPlotMode`` — plot artifacts from a prior SWMM run dir.

PRD-04. Migrated from ``OpenAIPlanner._run_existing_run_plot_workflow``.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.types import ToolCall
from agentic_swmm.agent.workflow_modes._helpers import (
    existing_run_plot_done_text,
    extract_plot_choice,
    plot_choice_prompt,
    plot_output_path,
)
from agentic_swmm.agent.workflow_modes._memory_hooks import (
    consult_memory,
    maybe_offer_onboarding_for_ctx,
)
from agentic_swmm.agent.workflow_modes.base import WorkflowContext, register


@register
class ExistingRunPlotMode:
    name = "existing_run_plot"
    required_inputs = ["run_dir"]
    recommended_next_tools = ["inspect_plot_options", "plot_run"]
    evidence_boundary = (
        "Plots generated from an existing run directory are visualization "
        "evidence from recorded SWMM artifacts."
    )

    def run(self, ctx: WorkflowContext):
        # Late import avoids the planner -> workflow_modes import cycle.
        from agentic_swmm.agent.planner import PlannerRun

        # Round 1 memory integration: consult to populate ctx.memory_context.
        # This mode does not run SWMM so no pre/postflight gate fires.
        consult_memory(ctx)

        # Round 7: plot-only mode never has an INP. The onboarding gate
        # still fires for new cases as long as the recommender resolves
        # any candidate attributes from the conventional layout.
        maybe_offer_onboarding_for_ctx(ctx, target_inp=None)

        run_dir = str(ctx.route.get("provided_values", {}).get("run_dir") or ctx.session_dir)

        options_result = ctx.step(ToolCall("inspect_plot_options", {"run_dir": run_dir}))
        if not options_result.get("ok"):
            return PlannerRun(
                ok=False,
                plan=ctx.plan,
                results=ctx.executor.results,
                final_text="Plot option inspection failed for the previous run directory.",
            )

        options = options_result.get("results") if isinstance(options_result.get("results"), dict) else {}
        plot_choice = extract_plot_choice(ctx.goal, options)
        if plot_choice is None:
            return PlannerRun(
                ok=True,
                plan=ctx.plan,
                results=ctx.executor.results,
                final_text=plot_choice_prompt(Path(run_dir), options),
            )

        plot_path = plot_output_path(Path(run_dir), plot_choice)
        plot_result = ctx.step(
            ToolCall(
                "plot_run",
                {"run_dir": run_dir, **plot_choice, "out_png": str(plot_path)},
            )
        )
        if not plot_result.get("ok"):
            return PlannerRun(
                ok=False,
                plan=ctx.plan,
                results=ctx.executor.results,
                final_text="Plot generation failed for the previous run directory.",
            )
        return PlannerRun(
            ok=True,
            plan=ctx.plan,
            results=ctx.executor.results,
            final_text=existing_run_plot_done_text(
                Path(run_dir), plot_choice, plot_path=plot_path
            ),
        )
