"""``PreparedDemoMode`` — smoke/benchmark demo with no required inputs."""

from __future__ import annotations

from agentic_swmm.agent.workflow_modes.base import register


@register
class PreparedDemoMode:
    name = "prepared_demo"
    required_inputs: list[str] = []
    recommended_next_tools = ["demo_acceptance", "audit_run"]
    evidence_boundary = (
        "Prepared demos are smoke or benchmark evidence, not proof of "
        "arbitrary greenfield modeling."
    )
