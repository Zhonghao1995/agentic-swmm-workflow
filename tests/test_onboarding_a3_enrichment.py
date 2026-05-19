"""PRD-08 A.3 (audit #17): chat block must inline proposed parameters + lesson headline."""
from __future__ import annotations

import pytest

from agentic_swmm.agent.onboarding import format_onboarding_chat_block
from agentic_swmm.memory.calibration_memory import CalibrationRecord
from agentic_swmm.memory.cross_watershed_transfer import TransferRecommendation


def _make_rec(
    *,
    proposed_parameters: dict[str, float] | None = None,
    known_failure_patterns: list[dict] | None = None,
    recommended_design_storm: dict | None = None,
) -> TransferRecommendation:
    return TransferRecommendation(
        target_case="saanich",
        source_case="tecnopolo",
        similarity=0.93,
        source_calibration_record=CalibrationRecord(
            run_id="r1",
            case_name="tecnopolo",
            objective_name="nse",
            objective_value=0.72,
        ),
        proposed_parameters=proposed_parameters or {},
        known_failure_patterns=known_failure_patterns or [],
        recommended_design_storm=recommended_design_storm,
    )


class TestProposedParameterInlining:
    def test_three_parameters_render_with_values(self):
        rec = _make_rec(
            proposed_parameters={
                "manning_n": 0.013,
                "depression_storage_mm": 2.5,
                "infiltration_max_rate_mmhr": 76.2,
            }
        )
        block = format_onboarding_chat_block("saanich", [rec])
        assert "Proposed:" in block
        assert "manning_n=0.013" in block
        assert "depression_storage_mm=2.5" in block
        assert "infiltration_max_rate_mmhr=76.2" in block

    def test_empty_proposed_parameters_omits_line(self):
        rec = _make_rec(proposed_parameters={})
        block = format_onboarding_chat_block("saanich", [rec])
        assert "Proposed:" not in block

    def test_caps_at_three_parameters(self):
        rec = _make_rec(
            proposed_parameters={
                "a": 1.0,
                "b": 2.0,
                "c": 3.0,
                "d": 4.0,
            }
        )
        block = format_onboarding_chat_block("saanich", [rec])
        # Sorted alphabetically: a, b, c selected; d dropped.
        assert "a=1" in block
        assert "b=2" in block
        assert "c=3" in block
        assert "d=4" not in block


class TestLessonHeadline:
    def test_single_lesson_renders_note(self):
        rec = _make_rec(
            known_failure_patterns=[
                {
                    "lesson_type": "continuity_breach",
                    "note": "runoff continuity exceeded 5% threshold when manning_n < 0.011",
                }
            ]
        )
        block = format_onboarding_chat_block("saanich", [rec])
        assert "Known pitfall in similar cases:" in block
        assert "from tecnopolo" in block
        assert "runoff continuity exceeded" in block

    def test_long_lesson_truncated(self):
        long_note = "x" * 200
        rec = _make_rec(
            known_failure_patterns=[{"lesson_type": "x", "note": long_note}]
        )
        block = format_onboarding_chat_block("saanich", [rec])
        # Note is truncated and ends with "..." somewhere
        assert "..." in block
        # Ensures we don't echo all 200 chars.
        assert "x" * 200 not in block

    def test_empty_lessons_omits_section(self):
        rec = _make_rec(known_failure_patterns=[])
        block = format_onboarding_chat_block("saanich", [rec])
        assert "Known pitfall" not in block


class TestDesignStormConditional:
    def test_design_storm_none_omits_line(self):
        rec = _make_rec(recommended_design_storm=None)
        block = format_onboarding_chat_block("saanich", [rec])
        assert "Recommended design storm" not in block

    def test_design_storm_present_renders_key(self):
        rec = _make_rec(recommended_design_storm={"key": "rome_100yr_3hr"})
        block = format_onboarding_chat_block("saanich", [rec])
        assert "Recommended design storm: rome_100yr_3hr." in block


class TestActionVocabulary:
    def test_prompt_uses_spaced_vocabulary(self):
        rec = _make_rec(proposed_parameters={"manning_n": 0.013})
        block = format_onboarding_chat_block("saanich", [rec])
        assert "[Y / n / customize]" in block
