"""``AuditOnlyOrComparisonMode`` — audit an existing SWMM run dir.

PRD-04. Migrated from ``OpenAIPlanner._run_audit_followup_workflow``.

The legacy keyword fallback in ``tool_registry._build_response_for_mode``
also appends ``baseline_run_dir`` to required inputs when the goal
mentions "compare"/"comparison"/"比较". That logic depends on goal
text rather than declarative state, so it stays at the call site.
"""

from __future__ import annotations

from agentic_swmm.agent.types import ToolCall
from agentic_swmm.agent.workflow_modes._memory_hooks import consult_memory
from agentic_swmm.agent.workflow_modes.base import WorkflowContext, register


@register
class AuditOnlyOrComparisonMode:
    name = "audit_only_or_comparison"
    required_inputs = ["run_dir"]
    recommended_next_tools = ["audit_run"]
    evidence_boundary = (
        "Audit records existing artifacts; it does not create missing "
        "SWMM execution evidence."
    )

    def run(self, ctx: WorkflowContext):
        # Late import avoids the planner -> workflow_modes import cycle.
        from agentic_swmm.agent.planner import PlannerRun

        # Round 1 memory integration: audit-mode reads existing run dirs
        # so no gate fires; the consult still populates ctx.memory_context
        # for any downstream decision hook the audit_run tool consults.
        consult_memory(ctx)

        run_dir = str(ctx.route.get("provided_values", {}).get("run_dir") or "")
        if not run_dir:
            return PlannerRun(
                ok=True,
                plan=ctx.plan,
                results=ctx.executor.results,
                final_text="Please provide a run directory to audit.",
            )
        result = ctx.step(
            ToolCall(
                "audit_run",
                {
                    "run_dir": run_dir,
                    "workflow_mode": "audit_only_or_comparison",
                    "objective": ctx.goal,
                },
            )
        )
        if not result.get("ok"):
            return PlannerRun(
                ok=False,
                plan=ctx.plan,
                results=ctx.executor.results,
                final_text="Audit failed; inspect the saved audit tool artifacts.",
            )
        return PlannerRun(
            ok=True,
            plan=ctx.plan,
            results=ctx.executor.results,
            final_text=f"Audit completed for {run_dir}.",
        )
