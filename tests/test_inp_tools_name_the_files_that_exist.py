"""The climate and propagation tools name the INP files that exist.

Live test 2026-09-03 (S46): run_climate_scenarios answered a guessed
06_runner/model.inp with "INP not found (in-repo paths only)" and the
planner lost a call finding 05_builder/model.inp.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from agentic_swmm.agent.tool_handlers.swmm_climate import run_climate_scenarios_tool
from agentic_swmm.agent.tool_handlers.swmm_uncertainty import propagate_parameter_ranges_tool
from agentic_swmm.agent.types import ToolCall


def _run_with_inp(tmp_path: Path) -> Path:
    run_dir = tmp_path / "071645_downtown-victoria-bc_run"
    (run_dir / "05_builder").mkdir(parents=True)
    (run_dir / "05_builder" / "model.inp").write_text("[TITLE]\n", encoding="utf-8")
    return run_dir


def test_climate_tool_names_the_existing_inp(tmp_path: Path) -> None:
    run_dir = _run_with_inp(tmp_path)
    call = ToolCall(
        name="run_climate_scenarios",
        args={"inp_path": str(run_dir / "06_runner" / "model.inp"), "factors": "1.0,1.3", "run_dir": str(run_dir)},
    )
    with mock.patch("agentic_swmm.agent.tool_handlers._shared.repo_root", return_value=tmp_path), mock.patch(
        "agentic_swmm.agent.tool_registry.repo_root", return_value=tmp_path
    ):
        result = run_climate_scenarios_tool(call, tmp_path)
    assert result["ok"] is False
    assert "05_builder/model.inp" in (result.get("hint") or "")


def test_propagation_tool_names_the_existing_inp(tmp_path: Path) -> None:
    run_dir = _run_with_inp(tmp_path)
    call = ToolCall(
        name="propagate_parameter_ranges",
        args={"inp_path": str(run_dir / "06_runner" / "model.inp"), "ranges": {"n_imperv": [0.01, 0.02]}, "run_dir": str(run_dir)},
    )
    with mock.patch("agentic_swmm.agent.tool_handlers._shared.repo_root", return_value=tmp_path), mock.patch(
        "agentic_swmm.agent.tool_registry.repo_root", return_value=tmp_path
    ):
        result = propagate_parameter_ranges_tool(call, tmp_path)
    assert result["ok"] is False
    assert "05_builder/model.inp" in (result.get("hint") or "")
