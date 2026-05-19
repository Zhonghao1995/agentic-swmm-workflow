"""PRD-08 A.3 (audit #21): chat_note title de-dup + user_prompt event handling."""
from __future__ import annotations

from agentic_swmm.audit.chat_note import build_chat_note


def test_title_collapsed_when_default_session_label():
    state: dict = {}
    trace: list[dict] = []
    note = build_chat_note(state, trace)
    # Header line must not be ``# Chat Session - Chat Session``.
    assert "# Chat Session - Chat Session" not in note
    assert "# Chat Session" in note


def test_title_preserved_when_case_id_present():
    state = {"case_id": "todcreek", "goal": "calibrate"}
    note = build_chat_note(state, [])
    assert "# Chat Session - todcreek" in note


def test_user_prompt_event_populates_what_user_asked():
    state = {"goal": "explore"}
    trace = [
        {"event": "user_prompt", "text": "show me the cases"},
        {"event": "user_prompt", "text": "now plot subcatchment SC1"},
    ]
    note = build_chat_note(state, trace)
    assert "show me the cases" in note
    assert "now plot subcatchment SC1" in note
    assert "(no user prompts recorded)" not in note


def test_no_user_prompt_events_falls_through_to_marker():
    state = {"goal": "explore"}
    note = build_chat_note(state, [])
    assert "(no user prompts recorded)" in note
