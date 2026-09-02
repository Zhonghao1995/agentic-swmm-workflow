"""The answer follows the language of the current message (F-45, 2026-09-02).

Right after a Chinese session, the English prompt "rainfall period November 4
to November 1" was diagnosed correctly but answered in Chinese: the
previous-session block carried the Chinese goal and nothing told the model
which language the person is writing in now.
"""

from __future__ import annotations

from agentic_swmm.agent.prompts import openai_planner_prompt


def test_the_base_prompt_pins_the_answer_language():
    prompt = openai_planner_prompt()
    assert "Answer in the language of the user's CURRENT message" in prompt
    assert "earlier sessions, memory blocks or artifacts are in another language" in prompt
