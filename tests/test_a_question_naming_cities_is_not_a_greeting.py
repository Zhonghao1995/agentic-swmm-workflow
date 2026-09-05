"""F-161 (S69, 2026-09-05): a question that names a thing of the trade reaches the model.

"Which cities can you fetch real municipal networks for?" got the canned
greeting in 0.3 s: the greeting check was a substring test ("hi" inside
"which", "yo" inside "you"), and a four-word "which cities are covered?"
fell to the short-prompt fallback. Greeting words now match whole words
and a domain noun makes a question, never a greeting.
"""

from __future__ import annotations

from agentic_swmm.agent.runtime_loop import is_open_shaped_prompt


def test_questions_naming_cities_networks_or_skills_are_not_greetings() -> None:
    for prompt in (
        "Which cities can you fetch real municipal networks for?",
        "which cities are covered?",
        "Which skills do you have?",
        "What areas of Canada are covered?",
        "你们覆盖哪些城市？",
    ):
        assert not is_open_shaped_prompt(prompt), prompt


def test_greetings_and_identity_probes_still_are() -> None:
    for prompt in ("hi", "hello there", "yo", "what can you do", "who are you", "你好"):
        assert is_open_shaped_prompt(prompt), prompt


def test_greeting_words_match_whole_words_only() -> None:
    assert not is_open_shaped_prompt("Which of these should I choose for my network?")
    assert not is_open_shaped_prompt("Could you tell me which model you would recommend?")
