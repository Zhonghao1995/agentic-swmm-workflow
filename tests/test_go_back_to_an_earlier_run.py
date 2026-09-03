"""A follow-up that names one earlier run of the session re-anchors there (F-71).

Live finding 2026-09-02 (scenario S21): "Go back to the downtown run and
export a Word report for it" wrote the report into the right run but left
the session anchored on the later James Bay run, so the turn's notes and
the digest footer belonged to the other model.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.intent_classifier import _run_slug_tokens, referenced_run_dir

DOWNTOWN = Path("runs/x/2026-09-02/221619_downtown-victoria-bc_run")
JAMES_BAY = Path("runs/x/2026-09-02/221937_james-bay-area-of-victoria_run")
DIRS = [DOWNTOWN, JAMES_BAY]


def test_slug_tokens_drop_the_stamp_the_kind_and_short_words():
    assert _run_slug_tokens(DOWNTOWN) == {"downtown", "victoria"}
    assert _run_slug_tokens(JAMES_BAY) == {"james", "victoria"}


def test_go_back_names_the_earlier_run():
    prompt = "Go back to the downtown run and export a Word report for it."
    assert referenced_run_dir(prompt, DIRS, current=JAMES_BAY) == DOWNTOWN


def test_shared_words_do_not_count():
    # "victoria" is in both slugs, so it identifies neither run.
    assert referenced_run_dir("Export a report for the Victoria run.", DIRS, current=JAMES_BAY) is None


def test_naming_both_runs_is_ambiguous():
    prompt = "Compare the James Bay run with the downtown run from earlier in this session."
    assert referenced_run_dir(prompt, DIRS, current=JAMES_BAY) is None


def test_naming_only_the_current_run_keeps_it():
    assert referenced_run_dir("Plot the outfall hydrograph of the James Bay run.", DIRS, current=JAMES_BAY) is None


def test_no_mention_and_no_runs():
    assert referenced_run_dir("Which node flooded the most?", DIRS, current=JAMES_BAY) is None
    assert referenced_run_dir("Go back to the downtown run.", [], current=None) is None


def test_the_shell_wires_the_helper():
    src = Path(__import__("agentic_swmm.agent.runtime_loop", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    assert "referenced_run_dir(prompt, session_run_dirs, active_run_dir[0])" in src
    assert "session_run_dirs.append(session_dir)" in src
