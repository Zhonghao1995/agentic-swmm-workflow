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


class WarmIntroFiresOncePerSessionRegression(unittest.TestCase):
    """Source-level guard for the 2026-05-16 ``turn = 0`` reset bug.

    Reproducer that motivated this guard: piping ``hi\\nhi\\nhello\\nwhat
    can you do\\n/exit`` to ``aiswmm`` fired the canned ``WARM_INTRO_
    TEMPLATE`` four times in 0.13 s (one per open-shaped prompt) without
    ever calling the LLM. Root cause: the warm-intro emit block in
    ``run_interactive_shell`` reset ``turn = 0`` after emitting, so the
    next ``turn += 1`` brought the counter back to ``1`` and
    ``maybe_warm_intro`` happily returned the same template again.

    Catching this at the unit level requires either (a) substantial
    mocking of ``run_interactive_shell``'s many collaborators or (b) a
    structural guard on the function source. We take (b) — fewer
    moving parts, fails loudly if the reset sneaks back in via copy-
    paste or a misguided "fix".
    """

    def _extract_warm_intro_block(self) -> str:
        """Return the warm-intro emit block with ``#``-comments stripped.

        Comments are removed so the assertion below tests *code*, not
        prose. Otherwise a comment quoting the removed reset (for
        provenance) would trigger a false positive.
        """
        import inspect
        import re

        from agentic_swmm.agent import runtime_loop

        source = inspect.getsource(runtime_loop.run_interactive_shell)
        lines = source.splitlines()
        captured: list[str] = []
        inside = False
        for line in lines:
            if "maybe_warm_intro(" in line:
                inside = True
            if inside:
                # Drop everything from ``#`` to end-of-line. The block
                # has no string literals containing ``#`` so this is
                # safe; if that ever changes, switch to ``tokenize``.
                code_only = re.sub(r"#.*$", "", line)
                captured.append(code_only)
                if code_only.strip() == "continue":
                    break
        return "\n".join(captured)

    def test_warm_intro_emit_block_does_not_reset_turn_counter(self) -> None:
        block = self._extract_warm_intro_block()
        self.assertTrue(
            block,
            "could not locate the warm-intro emit block in "
            "run_interactive_shell — refactor likely renamed the hook; "
            "update this regression guard.",
        )
        self.assertNotIn(
            "turn = 0",
            block,
            "warm-intro emit block resets ``turn = 0``; this re-arms the "
            "canned template for every subsequent open-shaped prompt. "
            "``maybe_warm_intro`` already enforces the ``turn == 1`` "
            "guard — let the counter advance naturally.",
        )


if __name__ == "__main__":
    unittest.main()
