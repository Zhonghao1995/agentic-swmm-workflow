"""The Word report's cover title follows the house style: no em dashes.

Live test 2026-09-03 (S34): the planner passed the title
"Downtown Victoria, BC \u2014 SWMM Simulation Report" and the deliverable
carried the em dash. The user's standing rule bans em dashes in every
artifact published on their behalf.
"""

from __future__ import annotations

from agentic_swmm.agent.tool_handlers.swmm_report import house_style_title, tool_specs


def test_spaced_em_dash_becomes_a_colon() -> None:
    assert house_style_title("Downtown Victoria, BC \u2014 SWMM Simulation Report") == (
        "Downtown Victoria, BC: SWMM Simulation Report"
    )


def test_unspaced_em_dash_and_spaced_en_dash_become_colons() -> None:
    assert house_style_title("Tod Creek\u2014Baseline") == "Tod Creek: Baseline"
    assert house_style_title("Tod Creek \u2013 Baseline") == "Tod Creek: Baseline"


def test_numeric_ranges_and_plain_titles_are_untouched() -> None:
    assert house_style_title("Rainfall Nov 1\u20134, 2023") == "Rainfall Nov 1\u20134, 2023"
    assert house_style_title("SWMM Simulation Report") == "SWMM Simulation Report"


def test_tool_spec_tells_the_planner_the_house_style() -> None:
    spec = next(s for s in tool_specs() if s.name == "generate_report")
    description = spec.parameters["properties"]["title"]["description"]
    assert "no em dashes" in description
