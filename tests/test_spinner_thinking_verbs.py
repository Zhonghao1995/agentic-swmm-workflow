"""THINKING spinner cycles verbs on its single status line (live-progress UX),
on a calm no-flicker cadence, without ever clobbering a RUNNING tool label.
Tests drive the pure ``_advance`` tick logic deterministically (no thread).
"""

from __future__ import annotations

import io

from agentic_swmm.agent.ui import Spinner, SpinnerState


def _thinking() -> Spinner:
    return Spinner("Thinking…", stream=io.StringIO(), state=SpinnerState.THINKING)


def test_thinking_spinner_cycles_through_several_verbs() -> None:
    s = _thinking()
    seen = {s.label}
    for _ in range(Spinner._VERB_TICKS * len(Spinner._THINKING_VERBS) + 2):
        s._advance()
        seen.add(s.label)
    assert len(seen) >= 3  # rotated through multiple verbs
    assert all(label.endswith("…") for label in seen)


def test_thinking_verb_cadence_is_calm_no_flicker() -> None:
    s = _thinking()
    for _ in range(Spinner._VERB_TICKS - 1):
        s._advance()
    assert s.label == "Thinking…"  # unchanged within the cadence window
    s._advance()  # crosses the cadence boundary
    assert s.label != "Thinking…"


def test_running_spinner_label_is_never_clobbered() -> None:
    s = Spinner("plot_run", stream=io.StringIO(), state=SpinnerState.RUNNING)
    for _ in range(Spinner._VERB_TICKS * 3):
        s._advance()
    assert s.label == "plot_run"  # verbs only apply to THINKING


def test_advance_spins_the_glyph() -> None:
    s = Spinner("x", stream=io.StringIO(), state=SpinnerState.RUNNING)
    start = s._frame
    s._advance()
    assert s._frame == (start + 1) % len(Spinner._FRAMES)
