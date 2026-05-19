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

    **PRD-02 follow-up.** The warm-intro state machine moved into
    :mod:`agentic_swmm.agent.warm_intro`. The new design replaces the
    integer turn counter with a one-way ``WarmIntroState.intro_emitted``
    flag, so the structural shape of the ``turn = 0`` regression class
    cannot recur (the only way to reset the flag is to construct a new
    ``WarmIntroState``, which is what ``/new-session`` does).

    The strict assertion below stays — it now greps
    ``warm_intro.py`` for any direct reset of ``intro_emitted = False``
    on a non-construction site. A behavioural reset would loosen the
    one-shot guarantee just as surely as the original ``turn = 0`` bug.
    """

    def _read_warm_intro_source(self) -> str:
        """Return the full source of ``warm_intro.py`` with ``#``-comments stripped.

        Comments are removed so the assertion below tests *code*, not
        prose. Otherwise a comment quoting the removed reset (for
        provenance) would trigger a false positive.
        """
        import inspect
        import re

        from agentic_swmm.agent import warm_intro

        source = inspect.getsource(warm_intro)
        code_only_lines: list[str] = []
        for line in source.splitlines():
            # Drop everything from ``#`` to end-of-line. ``warm_intro``
            # has no string literals containing ``#`` so this is
            # safe; if that ever changes, switch to ``tokenize``.
            code_only_lines.append(re.sub(r"#.*$", "", line))
        return "\n".join(code_only_lines)

    def test_warm_intro_module_never_unsets_intro_emitted_flag(self) -> None:
        """``warm_intro.py`` may flip ``intro_emitted`` True; never False.

        The one-shot lock is the structural replacement for the
        ``turn = 0`` reset bug — re-arming the template mid-session
        re-introduces the same bug class. Constructing a *new*
        ``WarmIntroState`` (e.g. on ``/new-session``) is the only
        sanctioned reset, and that happens in ``repl.py``, not here.
        """
        source = self._read_warm_intro_source()
        self.assertTrue(
            source,
            "could not locate warm_intro.py source — refactor likely "
            "moved the warm-intro state machine; update this regression "
            "guard.",
        )
        # The module is allowed to set the flag True (the one-shot emit).
        self.assertIn(
            "intro_emitted = True",
            source,
            "warm_intro.py must contain a one-shot emit that flips the "
            "flag — without it the gating is purely advisory and the "
            "original bug recurs.",
        )
        # The module must NOT re-arm the flag mid-life. Any False
        # assignment outside the dataclass default is a regression.
        self.assertNotIn(
            "intro_emitted = False",
            source,
            "warm_intro.py contains an explicit ``intro_emitted = False`` "
            "reset; this re-arms the canned template for the rest of "
            "the session. Construct a new WarmIntroState instead "
            "(``/new-session`` is the only sanctioned reset path).",
        )


if __name__ == "__main__":
    unittest.main()
