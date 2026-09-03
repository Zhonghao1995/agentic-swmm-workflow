"""A failed run stays the anchor, and no other run's results stand in for it.

Live test 2026-09-03 (S40 r3): the Regina fetch timed out, the run turn
failed, and the follow-up "what were the busiest conduits in that run"
opened a fresh chat that served an unrelated Victoria run's numbers as the
Regina result. Only successful turns set the pending state, so a failed run
left nothing to anchor the next question to.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent import runtime_loop
from agentic_swmm.agent.runtime_loop import FAILED_RUN_NOTE


def _source() -> str:
    return Path(runtime_loop.__file__).read_text(encoding="utf-8")


def test_the_note_says_no_results_and_forbids_substitution() -> None:
    assert "FAILED and produced no results" in FAILED_RUN_NOTE
    assert "Never present another run's results as this one" in FAILED_RUN_NOTE


def test_a_failed_run_turn_sets_the_pending_anchor() -> None:
    src = _source()
    block = src[src.index("elif not is_chat_turn and _is_swmm_run_dir(session_dir):") :]
    block = block[: block.index("print()")]
    assert '"failed": True' in block
    assert '"run_dir": str(session_dir)' in block


def test_the_continuation_carries_the_note_for_a_failed_run() -> None:
    src = _source()
    cont = src[src.index("elif pending is not None and not new_request:") :]
    cont = cont[: cont.index("elif active_run_dir[0] is not None and not new_request:")]
    assert 'if pending.get("failed"):' in cont
    assert "goal += FAILED_RUN_NOTE" in cont


def test_the_system_prompt_carries_the_rule() -> None:
    from agentic_swmm.agent import prompts

    text = Path(prompts.__file__).read_text(encoding="utf-8")
    assert "never present another run's results as that run" in text
