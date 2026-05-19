"""Workflow mode registry primitives (PRD-04 cycle 1).

The registry is the single source of truth for the workflow modes the
agent can dispatch. ``select_workflow_mode`` and the planner's
auto-route both look up the registry instead of carrying their own
hardcoded mapping (the "drift bug shape" that #117 surfaced).
"""

from __future__ import annotations

import pytest


def test_register_makes_mode_findable_by_name() -> None:
    from agentic_swmm.agent.workflow_modes import base

    @base.register
    class _MockMode:
        name = "mock_registry_probe"
        required_inputs = ["x"]
        recommended_next_tools = ["noop"]
        evidence_boundary = "Mock probe boundary."

    try:
        spec = base.get_mode_spec("mock_registry_probe")
        assert spec is not None
        assert spec.name == "mock_registry_probe"
        assert spec.required_inputs == ["x"]
        assert spec.recommended_next_tools == ["noop"]
        assert spec.evidence_boundary == "Mock probe boundary."
        assert "mock_registry_probe" in base.all_modes()
    finally:
        # Don't leak the probe into other tests' registry view.
        base._REGISTRY.pop("mock_registry_probe", None)


def test_get_mode_spec_returns_none_for_unknown_mode() -> None:
    from agentic_swmm.agent.workflow_modes import base

    assert base.get_mode_spec("does_not_exist") is None


def test_prepared_inp_spec_matches_hardcoded_table() -> None:
    """PRD-04 parity: ``PreparedInpMode`` spec must match the values
    the legacy hardcoded table in ``tool_registry.py`` published for
    ``mode == "prepared_inp_cli"``."""

    from agentic_swmm.agent.workflow_modes import get_mode_spec

    spec = get_mode_spec("prepared_inp_cli")
    assert spec is not None
    assert spec.name == "prepared_inp_cli"
    assert spec.required_inputs == ["inp_path"]
    assert spec.recommended_next_tools == [
        "run_swmm_inp",
        "audit_run",
        "inspect_plot_options",
        "plot_run",
    ]
    assert spec.evidence_boundary == (
        "Prepared INP execution is runnable/checkable/auditable evidence, "
        "not calibration or validation by itself."
    )


def test_existing_run_plot_spec_matches_hardcoded_table() -> None:
    from agentic_swmm.agent.workflow_modes import get_mode_spec

    spec = get_mode_spec("existing_run_plot")
    assert spec is not None
    assert spec.name == "existing_run_plot"
    assert spec.required_inputs == ["run_dir"]
    assert spec.recommended_next_tools == ["inspect_plot_options", "plot_run"]
    assert spec.evidence_boundary == (
        "Plots generated from an existing run directory are visualization "
        "evidence from recorded SWMM artifacts."
    )


def test_audit_only_or_comparison_spec_matches_hardcoded_table() -> None:
    """Base ``required_inputs`` is ``["run_dir"]``. The goal-text-dependent
    addition of ``baseline_run_dir`` for comparison-style prompts stays in
    ``_build_response_for_mode`` because it depends on the goal string,
    not on declarative spec state."""

    from agentic_swmm.agent.workflow_modes import get_mode_spec

    spec = get_mode_spec("audit_only_or_comparison")
    assert spec is not None
    assert spec.name == "audit_only_or_comparison"
    assert spec.required_inputs == ["run_dir"]
    assert spec.recommended_next_tools == ["audit_run"]
    assert spec.evidence_boundary == (
        "Audit records existing artifacts; it does not create missing "
        "SWMM execution evidence."
    )


def test_prepared_demo_spec_matches_hardcoded_table() -> None:
    """Prepared demos take no required user inputs (legacy explicitly
    sets ``required = []`` and forces ``missing = []`` regardless)."""

    from agentic_swmm.agent.workflow_modes import get_mode_spec

    spec = get_mode_spec("prepared_demo")
    assert spec is not None
    assert spec.name == "prepared_demo"
    assert spec.required_inputs == []
    assert spec.recommended_next_tools == ["demo_acceptance", "audit_run"]
    assert spec.evidence_boundary == (
        "Prepared demos are smoke or benchmark evidence, not proof of "
        "arbitrary greenfield modeling."
    )


def test_calibration_spec_matches_hardcoded_table() -> None:
    from agentic_swmm.agent.workflow_modes import get_mode_spec

    spec = get_mode_spec("calibration")
    assert spec is not None
    assert spec.name == "calibration"
    assert spec.required_inputs == ["inp_path", "observed_flow", "node"]
    assert spec.recommended_next_tools == ["run_swmm_inp", "audit_run"]
    assert spec.evidence_boundary == (
        "Calibration requires observed flow evidence and recorded "
        "parameter-selection artifacts; a successful run alone is not "
        "calibration."
    )


def test_uncertainty_spec_matches_hardcoded_table() -> None:
    from agentic_swmm.agent.workflow_modes import get_mode_spec

    spec = get_mode_spec("uncertainty")
    assert spec is not None
    assert spec.name == "uncertainty"
    assert spec.required_inputs == ["inp_path", "fuzzy_config", "node"]
    assert spec.recommended_next_tools == ["run_swmm_inp", "audit_run"]
    assert spec.evidence_boundary == (
        "Uncertainty runs produce scenario evidence, not calibrated "
        "predictive uncertainty unless supported by observed-data validation."
    )


def test_full_modular_build_spec_matches_hardcoded_table() -> None:
    from agentic_swmm.agent.workflow_modes import get_mode_spec

    spec = get_mode_spec("full_modular_build")
    assert spec is not None
    assert spec.name == "full_modular_build"
    assert spec.required_inputs == [
        "network_json",
        "subcatchments_csv",
        "rainfall_input",
        "landuse_input",
        "soil_input",
    ]
    assert spec.recommended_next_tools == [
        "format_rainfall",
        "network_qa",
        "build_inp",
        "run_swmm_inp",
        "audit_run",
    ]
    assert spec.evidence_boundary == (
        "Full modular build requires explicit GIS/network/rainfall/parameter "
        "inputs; the agent must not invent missing model inputs."
    )


def test_all_modes_returns_full_registry() -> None:
    """The registry must expose the full set of modes
    ``_select_workflow_mode_tool`` currently knows. Adding a new mode
    means adding one adapter file in ``workflow_modes/`` — the
    registry-walk auditability claim from PRD-04 depends on this."""

    from agentic_swmm.agent.workflow_modes import all_modes

    assert set(all_modes()) == {
        "audit_only_or_comparison",
        "calibration",
        "existing_run_plot",
        "full_modular_build",
        "prepared_demo",
        "prepared_inp_cli",
        "uncertainty",
    }


def test_dispatch_falls_back_when_registry_is_emptied() -> None:
    """PRD-04 deletion test: with the workflow-mode registry emptied,
    the planner's ``_dispatch_workflow_mode`` must return ``None`` for
    every previously dispatched mode (and so fall through to the LLM
    loop) rather than crash. This proves the dispatch site is not
    relying on a hardcoded fallback that would silently re-introduce
    the drift-bug shape PRD-04 set out to remove.

    The deletion-tested invariant is: with registry empty, NO mode is
    dispatched; the only way to gain dispatch back is to repopulate
    the registry. There is no hidden parallel code path.
    """

    import tempfile
    from pathlib import Path
    from unittest import mock

    from agentic_swmm.agent.executor import AgentExecutor
    from agentic_swmm.agent.permissions_profile import Profile
    from agentic_swmm.agent.planner import OpenAIPlanner
    from agentic_swmm.agent.tool_registry import AgentToolRegistry
    from agentic_swmm.agent.workflow_modes import base as _base

    with mock.patch.dict(_base._REGISTRY, {}, clear=True):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            executor = AgentExecutor(
                AgentToolRegistry(),
                session_dir=session_dir,
                trace_path=trace_path,
                dry_run=True,
                profile=Profile.QUICK,
            )
            planner = OpenAIPlanner(
                provider=None,  # type: ignore[arg-type]
                registry=AgentToolRegistry(),
                max_steps=1,
            )
            for mode in (
                "prepared_inp_cli",
                "existing_run_plot",
                "audit_only_or_comparison",
            ):
                dispatched = planner._dispatch_workflow_mode(
                    mode_name=mode,
                    goal="any",
                    session_dir=session_dir,
                    plan=[],
                    route={"mode": mode, "provided_values": {}},
                    executor=executor,
                )
                assert dispatched is None, (
                    f"With registry emptied, mode {mode!r} must not be dispatched; "
                    "any other behaviour would mean there is a hardcoded path the "
                    "registry refactor failed to remove."
                )
