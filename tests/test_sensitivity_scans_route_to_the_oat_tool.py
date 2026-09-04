"""Reference-free rankings go to the sweep tool's one-at-a-time mode; the
observed-flow sensitivity tools keep their data requirement.

Live test 2026-09-03: S48 answered a scan with five separate sweeps (F-107);
#509 then sent it to swmm_sensitivity_oat, which needs observed flow, and the
planner honestly returned nothing (F-109).
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.tool_handlers.swmm_uncertainty import tool_specs

REPO_ROOT = Path(__file__).resolve().parents[1]


def _spec(name: str):
    return next(s for s in tool_specs() if s.name == name)


def test_the_oat_tool_says_it_needs_observed_flow_and_names_the_alternative() -> None:
    description = _spec("swmm_sensitivity_oat").description
    assert "OBSERVED FLOW" in description
    assert "mode=one_at_a_time" in description


def test_the_sweep_tool_offers_the_one_at_a_time_mode() -> None:
    spec = _spec("propagate_parameter_ranges")
    assert spec.parameters["properties"]["mode"]["enum"] == ["joint", "one_at_a_time"]
    assert "which parameters matter most" in spec.description
    assert "Do not repeat this tool per parameter" in spec.description


def test_the_skill_states_the_honest_split() -> None:
    text = (REPO_ROOT / "skills" / "swmm-uncertainty" / "SKILL.md").read_text(encoding="utf-8")
    assert "The honest split" in text
    assert "WITH observed" in text and "WITHOUT observed flow" in text
    assert "mode=one_at_a_time" in text


def test_a_scaled_rain_event_routes_to_the_climate_tool() -> None:
    """Live test 2026-09-03 (S49): 'scale the event by 0.8/1.0/1.2' ran nothing."""
    from agentic_swmm.agent.tool_handlers.swmm_climate import tool_specs as climate_specs

    ensemble = _spec("swmm_rainfall_ensemble").description
    assert "PREPARED rainfall series file" in ensemble
    assert "run_climate_scenarios" in ensemble
    climate = next(s for s in climate_specs() if s.name == "run_climate_scenarios").description
    assert "rainfall-ensemble" in climate and "0.8,1.0,1.2" in climate
    text = (REPO_ROOT / "skills" / "swmm-uncertainty" / "SKILL.md").read_text(encoding="utf-8")
    assert "run_climate_scenarios" in text and "prepared rainfall series file" in text
