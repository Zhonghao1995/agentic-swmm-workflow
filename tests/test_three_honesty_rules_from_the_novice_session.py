"""Three prompt rules from the novice session (F-141, F-143, F-146).

Live test 2026-09-04, S63: "make it better" became plot + map without a
stated interpretation; "the pipes are in feet not meters" became a
fabricated unit conversion of a model delivered in metres; and "what did
you actually do in this session, and what did you refuse?" was answered
from the last run in hand, omitting six turns and three guard refusals.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent import prompts


def _text() -> str:
    return Path(prompts.__file__).read_text(encoding="utf-8")


def test_an_unbounded_request_states_its_interpretation() -> None:
    assert "names no measurable target" in _text()
    assert "states what it was taken to mean" in _text()
    # F-157 (S68, 2026-09-05): the figure was named but the headline read
    # "Outcome: Improved" although no model result changed.
    assert "never a verdict such as" in _text()
    assert "when no model result changed" in _text()
    # F-154 (S59 r2, 2026-09-05): the report step failed on the wheel and the
    # recap called the whole session FAILED although fetch, run and audit passed.
    assert "names the steps that passed and the deliverable that is missing" in _text()
    assert "never calls the whole session failed" in _text()


def test_a_units_claim_is_a_question_never_a_conversion() -> None:
    text = _text()
    assert "source data's units differ from the model's declared units" in text
    assert "never an edit or a conversion of a fetched model" in text


def test_a_session_question_answers_from_the_session_record() -> None:
    text = _text()
    assert "answered from the session record" in text
    assert "every guard that refused the assistant's own attempts" in text
