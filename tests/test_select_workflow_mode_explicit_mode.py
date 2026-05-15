"""Tests for the LLM-provided ``mode`` short-circuit in ``select_workflow_mode``.

PRD-INTENT-OVERMATCH (#95) Part 1: when the LLM has already classified the
user's intent it should be able to pass ``mode`` to the tool directly.
The tool then validates required inputs for that mode without re-deriving
intent via keyword matching. These tests lock the explicit-mode path.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.tool_registry import _select_workflow_mode_tool
from agentic_swmm.agent.types import ToolCall


def _run(goal: str, tmp_path: Path, **extras: str) -> dict:
    args: dict[str, object] = {"goal": goal}
    args.update(extras)
    call = ToolCall(name="select_workflow_mode", args=args)
    return _select_workflow_mode_tool(call, tmp_path)


def test_explicit_mode_overrides_keyword_match(tmp_path: Path) -> None:
    """Explicit ``mode="prepared_inp_cli"`` must win even when the goal text
    contains plot-adjacent vocabulary like ``rainfall``. The user-reported
    bug was that prompts mentioning rainfall got hijacked to plot mode."""

    payload = _run(
        "Run the SWMM input file at examples/foo.inp and audit rainfall peak",
        tmp_path,
        mode="prepared_inp_cli",
        inp_path="examples/foo.inp",
    )
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "prepared_inp_cli", payload


def test_explicit_calibration_mode_with_missing_inputs(tmp_path: Path) -> None:
    """Explicit ``mode="calibration"`` with insufficient inputs must report
    the calibration mode (not fall through to a different mode) and list
    the missing inputs."""

    payload = _run(
        "Calibrate the model",
        tmp_path,
        mode="calibration",
    )
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "calibration", payload
    missing = payload["results"]["missing_inputs"]
    assert "inp_path" in missing
    assert "observed_flow" in missing
    assert "node" in missing


def test_explicit_existing_run_plot_no_run_dir_does_not_auto_infer(
    tmp_path: Path,
) -> None:
    """When the LLM passes ``mode="existing_run_plot"`` but no ``run_dir``,
    the tool must report ``run_dir`` as missing. It must NOT silently
    pull an active run_dir from global state — that auto-infer behaviour
    was part of the original bug."""

    payload = _run(
        "Plot last run",
        tmp_path,
        mode="existing_run_plot",
    )
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "existing_run_plot", payload
    assert "run_dir" in payload["results"]["missing_inputs"]


def test_invalid_mode_falls_back_to_keyword_logic(tmp_path: Path) -> None:
    """An invalid ``mode`` value must fall through to the existing keyword
    fallback rather than raising or returning an error. This preserves
    forward compatibility if the LLM hallucinates a mode name."""

    payload = _run(
        "Run the SWMM input file",
        tmp_path,
        mode="bogus",
        inp_path="examples/foo.inp",
    )
    assert payload["ok"] is True
    # With ``inp_path`` provided and no plot-intent verbs, the keyword
    # fallback picks ``prepared_inp_cli``.
    assert payload["results"]["mode"] == "prepared_inp_cli", payload
