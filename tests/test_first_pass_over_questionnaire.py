"""A bounded analysis runs its first pass instead of asking a questionnaire (F-53).

Live finding 2026-09-02 (scenario S11): "How uncertain is the peak outflow?
Vary Manning's n and imperviousness and tell me the spread" ended, after 19
LLM calls, in four clarification questions whose answers the model had
already recommended itself. The product's north star is one tap.
"""

from __future__ import annotations

from agentic_swmm.agent.prompts import openai_planner_prompt


def test_the_base_prompt_prefers_a_first_pass_over_a_questionnaire():
    prompt = openai_planner_prompt()
    assert "run the first pass with those defaults" in prompt
    assert "ask at most ONE question" in prompt
    assert "Never answer a request with a questionnaire" in prompt
