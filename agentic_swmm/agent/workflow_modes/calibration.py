"""``CalibrationMode`` — spec-only stub.

The planner doesn't dispatch to a calibration adapter yet (no
``_run_calibration_workflow`` exists); the spec entry exists so
``select_workflow_mode`` can validate inputs and announce the evidence
boundary. Adding a real ``run`` method is a separate PRD.
"""

from __future__ import annotations

from agentic_swmm.agent.workflow_modes.base import register


@register
class CalibrationMode:
    name = "calibration"
    required_inputs = ["inp_path", "observed_flow", "node"]
    recommended_next_tools = ["run_swmm_inp", "audit_run"]
    evidence_boundary = (
        "Calibration requires observed flow evidence and recorded "
        "parameter-selection artifacts; a successful run alone is not "
        "calibration."
    )
