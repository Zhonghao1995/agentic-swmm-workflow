"""``UncertaintyMode`` — spec-only stub.

The planner doesn't dispatch a dedicated uncertainty adapter yet; the
spec entry exists so ``select_workflow_mode`` can validate inputs and
announce the boundary.
"""

from __future__ import annotations

from agentic_swmm.agent.workflow_modes.base import register


@register
class UncertaintyMode:
    name = "uncertainty"
    required_inputs = ["inp_path", "fuzzy_config", "node"]
    recommended_next_tools = ["run_swmm_inp", "audit_run"]
    evidence_boundary = (
        "Uncertainty runs produce scenario evidence, not calibrated "
        "predictive uncertainty unless supported by observed-data validation."
    )
