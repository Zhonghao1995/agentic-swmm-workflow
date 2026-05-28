"""Subscription-first interactive dispatch preflight.

``_preflight_interactive_dispatch`` rewrites the dispatched argv to the
rule planner only when the preflight reports *no usable provider*. With
subscription-first defaults the claude_sdk subscription path is always
selected (the SDK authenticates at call time), so the interactive
dispatch is left on ``--planner llm`` even when no credentials are
detected — but a soft warning is printed to stderr.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr

import pytest

from agentic_swmm.agent import provider_preflight
from agentic_swmm.cli import _preflight_interactive_dispatch


@pytest.fixture
def _no_keychain(monkeypatch):
    """Neutralise the macOS Keychain probe so the slate is known."""
    monkeypatch.setattr(
        provider_preflight,
        "_detect_macos_keychain_credentials",
        lambda: False,
        raising=True,
    )


def test_preflight_keeps_llm_planner_with_subscription_default(
    monkeypatch, tmp_path, _no_keychain
):
    # No credentials at all: the subscription default is still selected
    # (claude_sdk), so the planner is NOT downgraded to rule. A soft
    # warning lands on stderr pointing at `aiswmm login`.
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    err = io.StringIO()
    with redirect_stderr(err):
        argv = _preflight_interactive_dispatch(
            ["agent", "--planner", "llm", "--interactive"]
        )
    assert argv == ["agent", "--planner", "llm", "--interactive"]
    assert "aiswmm login" in err.getvalue()


def test_preflight_passes_through_when_openai_key_present(monkeypatch, tmp_path, _no_keychain):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    err = io.StringIO()
    with redirect_stderr(err):
        argv = _preflight_interactive_dispatch(
            ["agent", "--planner", "llm", "--interactive"]
        )
    assert argv == ["agent", "--planner", "llm", "--interactive"]
    assert err.getvalue() == ""


def test_preflight_passes_through_with_subscription(monkeypatch, tmp_path, _no_keychain):
    # A logged-in subscription user (ANTHROPIC_API_KEY signal here) keeps
    # the llm planner with no warning.
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    err = io.StringIO()
    with redirect_stderr(err):
        argv = _preflight_interactive_dispatch(
            ["agent", "--planner", "llm", "--interactive"]
        )
    assert argv == ["agent", "--planner", "llm", "--interactive"]
    assert err.getvalue() == ""


def test_preflight_does_not_swap_for_one_shot_invocations(monkeypatch, tmp_path, _no_keychain):
    """A one-shot ``agent <goal>`` without ``--interactive`` is left alone."""
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    argv = _preflight_interactive_dispatch(
        ["agent", "--planner", "llm", "what is the cite verb"]
    )
    # No --interactive => no swap; existing path retains.
    assert "--planner" in argv
    assert argv[argv.index("--planner") + 1] == "llm"
