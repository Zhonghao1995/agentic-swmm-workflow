"""A declined tool call ends the turn; it does not fail it (F-63).

Live finding 2026-09-02 (scenario S13d, `n` to the first approval): the
report said plainly that nothing ran, then the shell printed "Turn failed
with exit code 1. You can continue or type /exit."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic_swmm.agent import repl, runtime_loop


@dataclass
class _Outcome:
    ok: bool
    results: list[dict[str, Any]] = field(default_factory=list)


def test_a_declined_prompted_tool_gives_the_declined_code():
    outcome = _Outcome(ok=False, results=[{"tool": "fetch_swmm_from_canada", "ok": False, "permission": {"prompted": True, "approved": False}}])
    assert runtime_loop.turn_was_declined(outcome)
    assert runtime_loop._exit_code_for(outcome) == repl.DECLINED_EXIT_CODE == 3


def test_an_ordinary_failure_keeps_exit_code_one():
    outcome = _Outcome(ok=False, results=[{"tool": "run_swmm_inp", "ok": False, "permission": {"prompted": True, "approved": True}}])
    assert not runtime_loop.turn_was_declined(outcome)
    assert runtime_loop._exit_code_for(outcome) == 1


def test_success_is_zero_even_after_an_earlier_decline():
    outcome = _Outcome(ok=True, results=[{"tool": "x", "ok": False, "permission": {"prompted": True, "approved": False}}])
    assert runtime_loop._exit_code_for(outcome) == 0


def test_the_shell_says_declined_not_failed():
    src = open(repl.__file__, encoding="utf-8").read()
    assert "you declined a tool call, so nothing ran" in src
    assert "if rc == DECLINED_EXIT_CODE" in src
