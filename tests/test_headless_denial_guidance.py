"""A refusal must say why it refused and what would allow it.

v0.7.7 made approval fail closed when there is no human on the other end.
That is the right call. But the user only saw "tool not approved by
user", identical to the message a human gets after typing `n`, so a CI or
pipeline run learned neither the cause nor the documented opt-in.

The escape hatch was documented only in the `prompt_user` docstring,
which no user reads.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent import permissions
from agentic_swmm.agent.executor import DENIED_SUMMARY, AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


def _deny_with(reason: str):
    """Patch the approval seam to refuse for ``reason``."""
    return mock.patch.object(
        permissions,
        "request_approval",
        return_value=permissions.ApprovalDecision(approved=False, reason=reason),
    )


def _execute_write_under_safe_profile():
    """Run a write tool through a SAFE-profile executor and return the result."""
    with TemporaryDirectory() as tmp:
        executor = AgentExecutor(
            AgentToolRegistry(),
            session_dir=Path(tmp),
            trace_path=Path(tmp) / "trace.jsonl",
            dry_run=False,
            profile=Profile.SAFE,
        )
        return executor.execute(
            ToolCall(name="write_file", args={"path": "x.txt", "content": "y"})
        )


def test_no_human_present_is_reported_as_headless(monkeypatch):
    """Denial with no TTY must be distinguishable from a human saying no."""
    monkeypatch.delenv("AISWMM_AUTO_APPROVE", raising=False)
    monkeypatch.setattr(permissions.sys.stdin, "isatty", lambda: False)

    decision = permissions.request_approval("write_file")

    assert decision.approved is False
    assert decision.reason == "headless"
    assert decision.needs_guidance is True


def test_a_human_saying_no_gets_no_lecture(monkeypatch):
    """Someone who typed `n` meant it and needs no opt-in advice."""
    monkeypatch.delenv("AISWMM_AUTO_APPROVE", raising=False)
    monkeypatch.setattr(permissions.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    decision = permissions.request_approval("write_file")

    assert decision.approved is False
    assert decision.reason == "declined"
    assert decision.needs_guidance is False


def test_headless_denial_result_carries_the_opt_in():
    """The refusal a CI run sees must name the variable that permits it."""
    with _deny_with("headless"):
        result = _execute_write_under_safe_profile()

    assert result["ok"] is False
    assert "AISWMM_AUTO_APPROVE" in result["hint"]


def test_declined_denial_carries_no_hint():
    """A human who said no is not offered a way to overrule themselves."""
    with _deny_with("declined"):
        result = _execute_write_under_safe_profile()

    assert result["ok"] is False
    assert not result.get("hint")


def test_denial_summary_stays_byte_identical():
    """`run_failures` recognises denials by exact string equality.

    The guidance is additive precisely so that contract survives.
    """
    with _deny_with("headless"):
        result = _execute_write_under_safe_profile()

    assert result["summary"] == DENIED_SUMMARY == "tool not approved by user"


def test_the_user_actually_sees_the_hint(capsys):
    """Carrying guidance in the result is pointless if nothing prints it."""
    from agentic_swmm.agent import single_shot

    with _deny_with("headless"):
        result = _execute_write_under_safe_profile()
    single_shot._render_tool_outcome(index=1, total=1, name="write_file", result=result)

    assert "AISWMM_AUTO_APPROVE" in capsys.readouterr().out


def test_nothing_extra_prints_for_an_ordinary_failure(capsys):
    """Only a denial with no human present earns the extra line."""
    from agentic_swmm.agent import single_shot

    single_shot._render_tool_outcome(
        index=1,
        total=1,
        name="run_swmm_inp",
        result={"ok": False, "summary": "SWMM reported ERROR 138"},
    )

    assert "AISWMM_AUTO_APPROVE" not in capsys.readouterr().out
