"""A sensitivity scan routes to the OAT tool, a spread question to the sweep.

Live test 2026-09-03 (S48): "do a one-at-a-time sensitivity scan" was
answered with five separate propagate_parameter_ranges sweeps (25 SWMM runs)
although swmm_sensitivity_oat exists for exactly that question.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.tool_handlers.swmm_uncertainty import tool_specs

REPO_ROOT = Path(__file__).resolve().parents[1]


def _spec(name: str):
    return next(s for s in tool_specs() if s.name == name)


def test_the_oat_tool_says_it_answers_which_parameters_matter_in_one_call() -> None:
    description = _spec("swmm_sensitivity_oat").description
    assert "which parameters matter most" in description
    assert "ONE call" in description
    assert "propagate_parameter_ranges" in description


def test_the_sweep_tool_points_scans_at_the_oat_tool() -> None:
    description = _spec("propagate_parameter_ranges").description
    assert "swmm_sensitivity_oat" in description
    assert "instead of repeating this tool" in description


def test_the_skill_states_the_split() -> None:
    text = (REPO_ROOT / "skills" / "swmm-uncertainty" / "SKILL.md").read_text(encoding="utf-8")
    assert "Two typed tools, two questions" in text
    assert "swmm_sensitivity_oat" in text and "propagate_parameter_ranges" in text
