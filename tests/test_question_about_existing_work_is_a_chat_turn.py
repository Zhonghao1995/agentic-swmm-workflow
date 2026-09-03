"""A question about work already done opens a chat turn, not a run folder (F-74).

Live finding 2026-09-03 (scenario S25): after /new-session, "Which node
flooded the most in the downtown Victoria run I just made before the new
session, and for how long?" opened a run-kind folder that never received
a model, because the SWMM vocabulary read as a modeling request.
"""

from __future__ import annotations

import pytest

from agentic_swmm.agent.intent_classifier import is_question_about_existing_work, looks_like_swmm_request


@pytest.mark.parametrize(
    "prompt",
    [
        "Which node flooded the most in the downtown Victoria run I just made before the new session, and for how long?",
        "What were the three busiest conduits by peak flow in that run?",
        "How uncertain is the peak outflow of that run? Vary Manning's n and imperviousness.",
        "Is the observed data real or synthetic?",
        "哪个节点淹水最严重？淹了多久？",
    ],
)
def test_questions_about_existing_work_are_chat_turns(prompt):
    assert is_question_about_existing_work(prompt)
    assert looks_like_swmm_request(prompt) is False


@pytest.mark.parametrize(
    "prompt",
    [
        "Fetch a SWMM model from the Canada service for downtown Victoria BC and run it.",
        "Run examples/tecnopolo/tecnopolo_r1_199401.inp and audit it.",
        "Which node flooded the most in runs/x/05_builder/model.inp?",
        "Can you fetch a SWMM model for the James Bay bbox [-123.383, 48.414, -123.371, 48.423]?",
        "从加拿大服务抓取维多利亚市中心的SWMM模型，运行一下。",
    ],
)
def test_requests_for_new_work_are_still_modeling_requests(prompt):
    assert not is_question_about_existing_work(prompt)
    assert looks_like_swmm_request(prompt) is True


def test_a_leading_work_verb_behind_politeness_is_not_a_question():
    assert not is_question_about_existing_work("Can you run the tecnopolo demo?")
    assert not is_question_about_existing_work("请帮我 run examples/tecnopolo.inp")
