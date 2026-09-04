"""``permissions.request_decision`` records only an explicit y or n.

Live finding F-119 (2026-09-03, S54): the expert-review prompt reused the
tool-approval seam, where any key but y is a harmless decline; a stray
"/exit" therefore became a permanent "expert denied" record in an
archived run. A decision seam returns None for anything that is not a
decision, and the caller records nothing.
"""

from __future__ import annotations

import builtins
import sys

from agentic_swmm.agent import permissions


def _decide(monkeypatch, answer):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(permissions, "_prepare_prompt_line", lambda: None)
    monkeypatch.setattr(permissions, "_restore_after_prompt", lambda: None)
    if isinstance(answer, BaseException):
        def _raise(prompt=""):
            raise answer
        monkeypatch.setattr(builtins, "input", _raise)
    else:
        monkeypatch.setattr(builtins, "input", lambda prompt="": answer)
    return permissions.request_decision("Approve this result for decision use? [y/n] ")


def test_yes_and_no_are_decisions(monkeypatch) -> None:
    assert _decide(monkeypatch, "y") is True
    assert _decide(monkeypatch, " Yes ") is True
    assert _decide(monkeypatch, "n") is False
    assert _decide(monkeypatch, "NO") is False


def test_anything_else_is_no_decision(monkeypatch) -> None:
    for stray in ("", "/exit", "maybe", "q"):
        assert _decide(monkeypatch, stray) is None


def test_eof_is_no_decision(monkeypatch) -> None:
    assert _decide(monkeypatch, EOFError()) is None


def test_headless_stdin_is_no_decision(monkeypatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "y")
    assert permissions.request_decision("Approve? [y/n] ") is None
