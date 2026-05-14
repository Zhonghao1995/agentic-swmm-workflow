"""First-message warm self-intro contract (issue #59, open-shaped).

When the user's *first* message in an interactive session looks
open-shaped — a greeting (``你好`` / ``hi`` / ``hello`` / ``hey``), an
identity question (``what can you do`` / ``tell me about yourself``),
a short prompt (< 5 words), or anything lacking a task verb — the
runtime must emit the warm intro template *before* any tool call.

This covers the classifier (``is_open_shaped_prompt``) and the
template constant (``WARM_INTRO_TEMPLATE``) plus the env-var override
(``AISWMM_DISABLE_WELCOME=1`` skips the intro to stay consistent with
UX-2 #57).
"""

from __future__ import annotations

import os
import re
import unittest
from unittest import mock

from agentic_swmm.agent import prompts
from agentic_swmm.agent.runtime_loop import (
    is_open_shaped_prompt,
    maybe_warm_intro,
)


_INTRO_HEADLINE_RE = re.compile(
    r"I'?m Agentic SWMM,\s*your stormwater modeling collaborator",
    flags=re.IGNORECASE,
)


class OpenPromptClassifierTests(unittest.TestCase):
    """``is_open_shaped_prompt`` recognises greetings and identity probes."""

    def test_chinese_greeting_is_open_shaped(self) -> None:
        self.assertTrue(is_open_shaped_prompt("你好"))

    def test_english_greetings_are_open_shaped(self) -> None:
        for prompt in ("hi", "hello", "hey", "Hi!", "Hello there"):
            with self.subTest(prompt=prompt):
                self.assertTrue(is_open_shaped_prompt(prompt))

    def test_identity_questions_are_open_shaped(self) -> None:
        for prompt in (
            "what can you do",
            "what are you",
            "who are you?",
            "tell me about yourself",
            "Tell me what you do",
        ):
            with self.subTest(prompt=prompt):
                self.assertTrue(is_open_shaped_prompt(prompt))

    def test_short_prompt_under_five_words_is_open_shaped(self) -> None:
        # No task verb + fewer than 5 words → open-shaped fallback.
        for prompt in ("any tips", "guide me", "ready"):
            with self.subTest(prompt=prompt):
                self.assertTrue(is_open_shaped_prompt(prompt))

    def test_empty_or_whitespace_is_open_shaped(self) -> None:
        self.assertTrue(is_open_shaped_prompt(""))
        self.assertTrue(is_open_shaped_prompt("   "))


class WarmIntroTemplateTests(unittest.TestCase):
    """The warm-intro template lives at module scope on prompts.py."""

    def test_template_is_module_constant(self) -> None:
        self.assertTrue(hasattr(prompts, "WARM_INTRO_TEMPLATE"))
        self.assertIsInstance(prompts.WARM_INTRO_TEMPLATE, str)
        self.assertGreater(len(prompts.WARM_INTRO_TEMPLATE), 100)

    def test_template_introduces_identity(self) -> None:
        self.assertRegex(prompts.WARM_INTRO_TEMPLATE, _INTRO_HEADLINE_RE)

    def test_template_mentions_audit_trail(self) -> None:
        # The boundary discipline must show up in the intro itself —
        # warmth without scientific boundary would defeat the slice.
        self.assertIn("audit trail", prompts.WARM_INTRO_TEMPLATE)

    def test_template_offers_quickstart_examples(self) -> None:
        text = prompts.WARM_INTRO_TEMPLATE
        # Three quick-start handles from the PRD spec.
        self.assertIn("tecnopolo demo", text)
        self.assertIn("skills", text)


class MaybeWarmIntroBehaviour(unittest.TestCase):
    """``maybe_warm_intro`` is the runtime hook used on the first turn."""

    def test_open_shaped_first_message_emits_intro(self) -> None:
        # Strip env to make sure no test bleed-through disables this.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AISWMM_DISABLE_WELCOME", None)
            text = maybe_warm_intro("你好", turn=1)
        self.assertIsNotNone(text)
        assert text is not None  # for mypy
        self.assertRegex(text, _INTRO_HEADLINE_RE)

    def test_intro_only_fires_on_first_turn(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AISWMM_DISABLE_WELCOME", None)
            self.assertIsNone(maybe_warm_intro("你好", turn=2))
            self.assertIsNone(maybe_warm_intro("hi", turn=5))

    def test_disable_welcome_env_skips_intro(self) -> None:
        # Per issue #59 acceptance: AISWMM_DISABLE_WELCOME=1 also
        # suppresses the warm intro for consistency with UX-2 (#57).
        with mock.patch.dict(
            os.environ,
            {"AISWMM_DISABLE_WELCOME": "1"},
            clear=False,
        ):
            self.assertIsNone(maybe_warm_intro("你好", turn=1))
            self.assertIsNone(maybe_warm_intro("what can you do", turn=1))


if __name__ == "__main__":
    unittest.main()
