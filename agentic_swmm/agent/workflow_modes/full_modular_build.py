"""``FullModularBuildMode`` — spec-only stub.

The planner's dispatch table never carried a ``full_modular_build``
branch — the legacy code stops at announcing required inputs.
"""

from __future__ import annotations

from agentic_swmm.agent.workflow_modes.base import register


@register
class FullModularBuildMode:
    name = "full_modular_build"
    required_inputs = [
        "network_json",
        "subcatchments_csv",
        "rainfall_input",
        "landuse_input",
        "soil_input",
    ]
    recommended_next_tools = [
        "format_rainfall",
        "network_qa",
        "build_inp",
        "run_swmm_inp",
        "audit_run",
    ]
    evidence_boundary = (
        "Full modular build requires explicit GIS/network/rainfall/parameter "
        "inputs; the agent must not invent missing model inputs."
    )
