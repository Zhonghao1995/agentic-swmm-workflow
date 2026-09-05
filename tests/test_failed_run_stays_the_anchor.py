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
    assert "_failed_anchor(session_dir, final_text)" in block


def test_the_anchor_carries_the_failed_flag_and_the_run_dir(tmp_path) -> None:
    anchor = runtime_loop._failed_anchor(tmp_path, "x" * 500)
    assert anchor["failed"] is True
    assert anchor["is_chat"] is False
    assert anchor["run_dir"] == str(tmp_path)
    assert anchor["session_dir"] == tmp_path
    assert len(anchor["tail"]) == 400


def test_an_interrupted_run_turn_sets_the_same_anchor() -> None:
    # F-159 (S27 r2, 2026-09-05): the exception path set no anchor at all, so
    # the next question about "that run" borrowed another session's run.
    src = _source()
    block = src[src.index("except BaseException as exc:") :]
    block = block[: block.index("raise")]
    assert 'finalize_session_header(session_dir, "interrupted")' in block
    assert "if not is_chat_turn and _is_swmm_run_dir(session_dir):" in block
    assert '_failed_anchor(session_dir, f"error: {exc}")' in block


def test_the_shell_does_not_blame_the_provider_for_every_crash() -> None:
    # F-158: an upstream build timeout was answered with "Check the provider".
    from agentic_swmm.agent import repl

    assert "Check the provider" not in repl.TURN_STOPPED_HINT
    assert "stopped before it finished" in repl.TURN_STOPPED_HINT
    assert "aiswmm doctor" in repl.TURN_STOPPED_HINT


def test_the_prompt_names_the_gap_instead_of_borrowing_a_run() -> None:
    from agentic_swmm.agent import prompts

    text = Path(prompts.__file__).read_text(encoding="utf-8")
    assert "never takes a run from memory or from another session as that run" in text


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
