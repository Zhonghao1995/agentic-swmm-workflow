"""Asked what it remembers, the planner answers from the memory blocks (F-69).

Live finding 2026-09-02 (scenario S19): on "what do you remember about
today's downtown Victoria runs", the memory arm re-ran audit_run (13 tool
calls, 150k tokens, one approval) instead of answering from the
<parametric-memory> block, while the no-memory arm found the same facts on
disk for 100k tokens. The blocks were there; the prompt never said to use
them first.
"""

from __future__ import annotations

from agentic_swmm.agent.prompts import openai_planner_prompt


def test_the_base_prompt_says_to_answer_memory_questions_from_the_blocks():
    prompt = openai_planner_prompt()
    assert "answer from the memory blocks in this prompt" in prompt
    assert "<parametric-memory>" in prompt and "<recent-failures>" in prompt
    assert "call tools only to verify a fact those blocks do not contain" in prompt
