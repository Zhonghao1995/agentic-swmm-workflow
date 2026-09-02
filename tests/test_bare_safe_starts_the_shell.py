"""``aiswmm --safe`` starts the safe interactive shell (F-47, 2026-09-02).

The welcome banner advertises ``--safe``; typed bare, it was routed to a
one-shot agent session with an empty goal, which defaults to ``run doctor``:
the shell never appeared and a doctor approval question did.
"""

from __future__ import annotations

from agentic_swmm.cli import _agent_options_without_goal, _route_default_to_agent


def test_bare_safe_is_an_interactive_shell():
    assert _agent_options_without_goal(["--safe"]) is True
    assert "--interactive" in _route_default_to_agent(["--safe"])


def test_session_dir_with_safe_is_still_the_shell():
    argv = ["--session-dir", "runs/x", "--safe"]
    assert _agent_options_without_goal(argv) is True
    routed = _route_default_to_agent(argv)
    assert "--interactive" in routed and "--safe" in routed


def test_case_id_takes_a_value():
    assert _agent_options_without_goal(["--case-id", "tod-creek", "--safe"]) is True


def test_a_goal_after_options_is_still_a_goal():
    assert _agent_options_without_goal(["--safe", "run my model"]) is False
    assert "--interactive" not in _route_default_to_agent(["--safe", "run my model"])
