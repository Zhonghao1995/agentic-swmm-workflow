"""PRD-08 A.3 (audit #6): bare ``aiswmm`` should preflight provider."""
from __future__ import annotations

import io
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from agentic_swmm.cli import _preflight_interactive_dispatch


def test_preflight_swaps_planner_to_rule_when_no_key(monkeypatch, tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    err = io.StringIO()
    with redirect_stderr(err):
        argv = _preflight_interactive_dispatch(
            ["agent", "--planner", "openai", "--interactive"]
        )
    assert argv == ["agent", "--planner", "rule", "--interactive"]
    assert "No LLM provider configured" in err.getvalue()


def test_preflight_passes_through_when_key_present(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    err = io.StringIO()
    with redirect_stderr(err):
        argv = _preflight_interactive_dispatch(
            ["agent", "--planner", "openai", "--interactive"]
        )
    assert argv == ["agent", "--planner", "openai", "--interactive"]
    assert err.getvalue() == ""


def test_preflight_does_not_swap_for_one_shot_invocations(monkeypatch, tmp_path):
    """A one-shot ``agent <goal>`` without ``--interactive`` is left alone."""
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    argv = _preflight_interactive_dispatch(
        ["agent", "--planner", "openai", "what is the cite verb"]
    )
    # No --interactive => no swap; existing error path retains.
    assert "--planner" in argv
    assert argv[argv.index("--planner") + 1] == "openai"
