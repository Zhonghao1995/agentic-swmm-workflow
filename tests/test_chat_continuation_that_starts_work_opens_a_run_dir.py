"""A chat continuation that starts modeling work opens a run folder (F-75).

Live finding 2026-09-03 (scenario S26): "Where do I start?" was a chat turn;
the answer "It's the downtown core ... Just get me a first model for last
November's rain" continued in the chat folder and the fetched model was
nested under it, invisible to the date-level run listing, the metrics
table, compare-by-dir and session recall.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.intent_classifier import looks_like_new_modeling_request

ANSWER = "It's the downtown core, a few blocks around Douglas Street and Fort Street. Just get me a first model for last November's rain."
BBOX = "[-123.425, 48.428, -123.413, 48.437]"


def test_a_chat_answer_with_a_work_verb_starts_work():
    assert looks_like_new_modeling_request(ANSWER, answering_question=True, in_chat=True) is True


def test_a_chat_turn_that_ended_with_a_statement_still_starts_work():
    # S26 r2: the guidance ended with a statement, not a question, so the
    # shell did not treat the reply as an answer; the chat rule must not
    # depend on that flag.
    assert looks_like_new_modeling_request(ANSWER, answering_question=False, in_chat=True) is True
    assert looks_like_new_modeling_request(BBOX, answering_question=False, in_chat=True) is True


def test_the_same_answer_with_a_run_in_hand_continues_that_run():
    # F-07 behaviour is unchanged: "it" points back at the run in hand.
    assert looks_like_new_modeling_request(ANSWER, answering_question=True, in_chat=False) is False


def test_a_bare_bbox_answer_starts_work_only_in_chat():
    assert looks_like_new_modeling_request(BBOX, answering_question=True, in_chat=True) is True
    assert looks_like_new_modeling_request(BBOX, answering_question=True, in_chat=False) is False


def test_a_plain_answer_in_chat_stays_a_chat_turn():
    assert looks_like_new_modeling_request("Downtown, near the harbour.", answering_question=True, in_chat=True) is False


def test_the_shell_passes_the_chat_flag_and_keeps_the_conversation():
    src = Path(__import__("agentic_swmm.agent.runtime_loop", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    assert 'in_chat=bool(pending.get("is_chat", False))' in src
    assert "the work was asked for in answer to a" in src
