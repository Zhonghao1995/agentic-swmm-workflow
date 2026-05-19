"""Warm-intro state machine (PRD-02): one-shot emit guarded by explicit state.

The 2026-05-16 ``turn = 0`` reset bug (issue #108) happened because the
warm-intro decision was scattered across two places: a pure
``maybe_warm_intro(prompt, turn=int)`` and a caller in ``runtime_loop``
that managed ``turn``. The fix moves the lock into a single explicit
state object whose ``intro_emitted`` flag can only transition False ->
True. That mutation is structurally one-way: re-arming requires either
constructing a *new* ``WarmIntroState`` (which is what ``/new-session``
explicitly does) or assigning ``False`` to ``state.intro_emitted`` —
something no REPL code path does.
"""

from __future__ import annotations

import os
import re
import unittest
from unittest import mock

from agentic_swmm.agent.warm_intro import (
    WarmIntroState,
    maybe_emit_warm_intro,
)


_INTRO_HEADLINE_RE = re.compile(
    r"I'?m Agentic SWMM,\s*your stormwater modeling collaborator",
    flags=re.IGNORECASE,
)


class WarmIntroStateInitialiserTests(unittest.TestCase):
    """``WarmIntroState`` starts with ``intro_emitted = False``."""

    def test_default_state_has_not_emitted(self) -> None:
        state = WarmIntroState()
        self.assertFalse(state.intro_emitted)


class OpenShapedFirstPromptEmitsAndMutatesTests(unittest.TestCase):
    """First open-shaped prompt returns template AND flips the flag.

    This is the happy path: greeting / identity probe arrives,
    ``maybe_emit_warm_intro`` returns the canned template, and the
    state has transitioned to ``intro_emitted = True`` so future
    calls in the same session won't re-emit.
    """

    def test_open_shaped_emits_template_and_sets_flag(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AISWMM_DISABLE_WELCOME", None)
            state = WarmIntroState()
            text = maybe_emit_warm_intro(state, "你好")
        self.assertIsNotNone(text)
        assert text is not None  # mypy
        self.assertRegex(text, _INTRO_HEADLINE_RE)
        self.assertTrue(state.intro_emitted)


class WarmIntroOneShotLockTests(unittest.TestCase):
    """Once ``intro_emitted`` is True, no prompt re-arms the template.

    This is the **structural** replacement for the 2026-05-16
    ``turn = 0`` reset regression. The previous design used an
    integer turn counter that the caller could mistakenly reset;
    the new design exposes only a boolean that the caller never
    has reason to flip back. ``maybe_emit_warm_intro`` will not
    decrement it.
    """

    def test_second_open_shaped_prompt_returns_none(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AISWMM_DISABLE_WELCOME", None)
            state = WarmIntroState()
            # First emit fires.
            self.assertIsNotNone(maybe_emit_warm_intro(state, "hi"))
            # Subsequent open-shaped prompts return None — even the
            # exact replay that caused the original bug.
            self.assertIsNone(maybe_emit_warm_intro(state, "hi"))
            self.assertIsNone(maybe_emit_warm_intro(state, "hello"))
            self.assertIsNone(maybe_emit_warm_intro(state, "what can you do"))
            self.assertIsNone(maybe_emit_warm_intro(state, "你好"))

    def test_already_emitted_state_returns_none_on_construction(self) -> None:
        """Passing in a pre-locked state never emits."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AISWMM_DISABLE_WELCOME", None)
            state = WarmIntroState(intro_emitted=True)
            self.assertIsNone(maybe_emit_warm_intro(state, "hi"))
            self.assertTrue(state.intro_emitted)


class TaskShapedPromptsLeaveStateUnchangedTests(unittest.TestCase):
    """A task-shaped prompt returns None AND does NOT lock the state.

    Important: when the user's first turn is task-shaped (e.g.
    ``run tecnopolo demo``), we skip the intro but we *do not*
    consume the one-shot. If the second turn is open-shaped
    (``what else can you do?``), the intro must still be able to
    fire.
    """

    def test_task_prompt_returns_none(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AISWMM_DISABLE_WELCOME", None)
            state = WarmIntroState()
            self.assertIsNone(maybe_emit_warm_intro(state, "run tecnopolo demo"))
            self.assertIsNone(maybe_emit_warm_intro(state, "build inp from subcatchments.csv"))
            self.assertIsNone(maybe_emit_warm_intro(state, "calibrate against observed"))

    def test_task_prompt_keeps_intro_armed_for_later_open_shaped(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AISWMM_DISABLE_WELCOME", None)
            state = WarmIntroState()
            self.assertIsNone(maybe_emit_warm_intro(state, "run tecnopolo demo"))
            self.assertFalse(state.intro_emitted)
            # Later open-shaped prompt in same session still emits.
            text = maybe_emit_warm_intro(state, "what can you do")
            self.assertIsNotNone(text)
            self.assertTrue(state.intro_emitted)


class DisableWelcomeEnvSkipsIntroTests(unittest.TestCase):
    """``AISWMM_DISABLE_WELCOME=1`` mirrors UX-2 #57 by suppressing the intro."""

    def test_disable_welcome_returns_none_even_on_greeting(self) -> None:
        with mock.patch.dict(
            os.environ, {"AISWMM_DISABLE_WELCOME": "1"}, clear=False
        ):
            state = WarmIntroState()
            self.assertIsNone(maybe_emit_warm_intro(state, "你好"))
            self.assertIsNone(maybe_emit_warm_intro(state, "what can you do"))
            # And the state stays unlocked (env-disabled is not a commit).
            self.assertFalse(state.intro_emitted)


if __name__ == "__main__":
    unittest.main()
