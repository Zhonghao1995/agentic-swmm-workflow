"""Warm-intro state machine for the interactive REPL (PRD-02).

The previous implementation in ``runtime_loop.py`` carried the
warm-intro decision in two places: a pure
``maybe_warm_intro(prompt, turn=int)`` predicate and an integer
``turn`` counter that lived on the REPL stack. That split made
issue #108's ``turn = 0`` reset bug possible: the REPL reset the
counter after emitting, which re-armed the canned template on
every subsequent open-shaped prompt.

This module collapses both halves into one explicit state object
whose ``intro_emitted`` flag is *one-way*: once True, the only way
to re-arm is to construct a fresh ``WarmIntroState``. That is
exactly what ``/new-session`` does, and nothing else has a reason
to.

Deep-module note: ``WarmIntroState`` is the entire public state.
The single decision function (``maybe_emit_warm_intro``) reads the
flag, defers vocabulary to ``intent_classifier``, and on emit
flips the flag closed. There is no other gate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agentic_swmm.agent.intent_classifier import classify_intent
from agentic_swmm.agent.prompts import WARM_INTRO_TEMPLATE

__all__ = [
    "WarmIntroState",
    "WARM_INTRO_TEMPLATE",
    "maybe_emit_warm_intro",
]


@dataclass
class WarmIntroState:
    """Session-scope state for the warm-intro one-shot.

    ``intro_emitted`` starts False and is flipped True the first
    time ``maybe_emit_warm_intro`` returns the template. Once
    True, every subsequent call returns None — the lock is
    structural and can only be released by replacing the state
    object (e.g. on ``/new-session``).
    """

    intro_emitted: bool = False


def _welcome_disabled() -> bool:
    """Mirror ``welcome._is_disabled`` so the same env var controls both."""

    value = os.environ.get("AISWMM_DISABLE_WELCOME")
    if value is None:
        return False
    return value.strip() not in {"", "0", "false", "False", "no", "No"}


def maybe_emit_warm_intro(state: WarmIntroState, prompt: str) -> str | None:
    """Decide whether to emit the warm intro and mutate ``state`` if so.

    Returns the warm-intro template text on emit, or ``None`` when:

    - ``state.intro_emitted`` is already True (one-shot lock),
    - ``AISWMM_DISABLE_WELCOME=1`` is set in the environment,
    - the prompt is task-shaped (intent_classifier returns
      ``is_open_shaped=False``).

    On emit, ``state.intro_emitted`` is set to True before
    returning so the caller cannot forget to commit.
    """

    if state.intro_emitted:
        return None
    if _welcome_disabled():
        return None
    if not classify_intent(prompt).is_open_shaped:
        return None
    state.intro_emitted = True
    return WARM_INTRO_TEMPLATE
