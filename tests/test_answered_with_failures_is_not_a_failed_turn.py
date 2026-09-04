"""An answered turn with failed calls is reported as that, not as a failed turn (F-123).

Live finding 2026-09-03 (scenario S55, turn 2): two read-only MCP listings
failed, the planner answered honestly ("two servers did not complete
discovery, counts unverified"), and the shell printed "Turn failed with
exit code 1. You can continue or type /exit." under the complete answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic_swmm.agent import repl, runtime_loop


@dataclass
class _Outcome:
    ok: bool
    results: list[dict[str, Any]] = field(default_factory=list)
    final_text: str = ""


def test_an_answer_over_failed_calls_gets_its_own_code():
    outcome = _Outcome(
        ok=False,
        results=[{"tool": "list_mcp_tools", "ok": False, "permission": {"prompted": False, "approved": True}}],
        final_text="11 servers are configured; two did not complete discovery.",
    )
    assert runtime_loop.turn_answered_with_failures(outcome)
    assert runtime_loop._exit_code_for(outcome) == repl.ANSWERED_WITH_FAILURES_EXIT_CODE == 4


def test_no_answer_keeps_exit_code_one():
    outcome = _Outcome(ok=False, results=[{"tool": "run_swmm_inp", "ok": False}], final_text="")
    assert not runtime_loop.turn_answered_with_failures(outcome)
    assert runtime_loop._exit_code_for(outcome) == 1


def test_a_declined_turn_stays_declined():
    outcome = _Outcome(
        ok=False,
        results=[{"tool": "fetch_swmm_from_canada", "ok": False, "permission": {"prompted": True, "approved": False}}],
        final_text="Nothing ran because the fetch was declined.",
    )
    assert runtime_loop._exit_code_for(outcome) == repl.DECLINED_EXIT_CODE


def test_an_answer_with_no_failed_call_is_not_this_case():
    outcome = _Outcome(ok=False, results=[{"tool": "x", "ok": True}], final_text="planner stopped after max_steps=8")
    assert not runtime_loop.turn_answered_with_failures(outcome)
    assert runtime_loop._exit_code_for(outcome) == 1


def test_the_shell_names_the_case():
    src = open(repl.__file__, encoding="utf-8").read()
    assert "elif rc == ANSWERED_WITH_FAILURES_EXIT_CODE" in src
    assert "Turn ended with unresolved tool failure(s)" in src
