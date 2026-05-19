"""``_select_workflow_mode_tool`` parity across the registry migration.

PRD-04. ``_build_response_for_mode`` used to carry a hardcoded
``if mode == "X"`` table publishing ``required_inputs`` /
``recommended_next_tools`` / ``evidence_boundary``. After the refactor
the table lives in ``agent/workflow_modes/*.py`` and
``_build_response_for_mode`` looks the values up via the registry.
Every byte of the tool's response payload must remain identical or the
LLM-facing contract changes silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_swmm.agent.tool_registry import _select_workflow_mode_tool
from agentic_swmm.agent.types import ToolCall


def _run(goal: str, tmp_path: Path, **extras: str) -> dict:
    args: dict[str, object] = {"goal": goal}
    args.update(extras)
    call = ToolCall(name="select_workflow_mode", args=args)
    return _select_workflow_mode_tool(call, tmp_path)


@pytest.mark.parametrize(
    ("mode", "expected_required", "expected_next_tools", "expected_boundary"),
    [
        (
            "prepared_inp_cli",
            ["inp_path"],
            ["run_swmm_inp", "audit_run", "inspect_plot_options", "plot_run"],
            (
                "Prepared INP execution is runnable/checkable/auditable evidence, "
                "not calibration or validation by itself."
            ),
        ),
        (
            "existing_run_plot",
            ["run_dir"],
            ["inspect_plot_options", "plot_run"],
            (
                "Plots generated from an existing run directory are visualization "
                "evidence from recorded SWMM artifacts."
            ),
        ),
        (
            "audit_only_or_comparison",
            ["run_dir"],
            ["audit_run"],
            (
                "Audit records existing artifacts; it does not create missing "
                "SWMM execution evidence."
            ),
        ),
        (
            "prepared_demo",
            [],
            ["demo_acceptance", "audit_run"],
            (
                "Prepared demos are smoke or benchmark evidence, not proof of "
                "arbitrary greenfield modeling."
            ),
        ),
        (
            "calibration",
            ["inp_path", "observed_flow", "node"],
            ["run_swmm_inp", "audit_run"],
            (
                "Calibration requires observed flow evidence and recorded "
                "parameter-selection artifacts; a successful run alone is not "
                "calibration."
            ),
        ),
        (
            "uncertainty",
            ["inp_path", "fuzzy_config", "node"],
            ["run_swmm_inp", "audit_run"],
            (
                "Uncertainty runs produce scenario evidence, not calibrated "
                "predictive uncertainty unless supported by observed-data validation."
            ),
        ),
        (
            "full_modular_build",
            [
                "network_json",
                "subcatchments_csv",
                "rainfall_input",
                "landuse_input",
                "soil_input",
            ],
            [
                "format_rainfall",
                "network_qa",
                "build_inp",
                "run_swmm_inp",
                "audit_run",
            ],
            (
                "Full modular build requires explicit GIS/network/rainfall/parameter "
                "inputs; the agent must not invent missing model inputs."
            ),
        ),
    ],
)
def test_explicit_mode_response_uses_registry_values(
    tmp_path: Path,
    mode: str,
    expected_required: list[str],
    expected_next_tools: list[str],
    expected_boundary: str,
) -> None:
    """Pin the LLM-facing response shape per mode.

    All seven modes must echo the registered ``required_inputs`` /
    ``recommended_next_tools`` / ``evidence_boundary`` exactly. The
    LLM sees this payload — silently changing it is a contract break.
    """

    payload = _run("does not matter", tmp_path, mode=mode)
    result = payload["results"]
    assert result["mode"] == mode
    assert result["required_inputs"] == expected_required
    # ``recommended_next_tools`` is suppressed when ``missing`` is
    # non-empty. For modes with required inputs the no-args case
    # produces a missing list, so recommended_next_tools is []. For
    # ``prepared_demo`` and any mode with empty required_inputs the
    # next_tools are echoed in full.
    if expected_required:
        assert result["recommended_next_tools"] == []
    else:
        assert result["recommended_next_tools"] == expected_next_tools
    assert result["evidence_boundary"] == expected_boundary


def test_explicit_audit_comparison_appends_baseline_run_dir(tmp_path: Path) -> None:
    """The compound-audit branch (goal contains 'compare'/'comparison'/
    '比较') still appends ``baseline_run_dir`` to required inputs. This
    rule is goal-text-dependent so it stays at the call site instead of
    on the adapter class."""

    payload = _run(
        "please compare runs/foo to runs/bar",
        tmp_path,
        mode="audit_only_or_comparison",
        run_dir="runs/foo",
    )
    required = payload["results"]["required_inputs"]
    assert "run_dir" in required
    assert "baseline_run_dir" in required
