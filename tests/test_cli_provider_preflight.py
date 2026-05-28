"""Interactive-dispatch preflight (two API keys).

``_preflight_interactive_dispatch`` keeps the LLM planner whenever the
preflight reports a usable (known) provider — which the shipped openai
default always is — and only rewrites the dispatched argv to the rule
planner when the resolved default is an *unknown* provider. When the
selected provider has no detectable key a soft warning lands on stderr.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr

from agentic_swmm.cli import _preflight_interactive_dispatch


def test_preflight_keeps_llm_planner_with_openai_default_no_key(monkeypatch, tmp_path):
    # No key at all: the openai default is still a usable (known)
    # provider, so the planner is NOT downgraded to rule. A soft warning
    # lands on stderr pointing at `aiswmm login`.
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


def test_preflight_passes_through_when_openai_key_present(monkeypatch, tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    err = io.StringIO()
    with redirect_stderr(err):
        argv = _preflight_interactive_dispatch(
            ["agent", "--planner", "llm", "--interactive"]
        )
    assert argv == ["agent", "--planner", "llm", "--interactive"]
    assert err.getvalue() == ""


def test_preflight_passes_through_when_anthropic_default_with_key(monkeypatch, tmp_path):
    # anthropic pinned as default with its key present -> usable, no warning.
    home = tmp_path / "h"
    home.mkdir()
    cfg = home / ".aiswmm"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text('[provider]\ndefault = "anthropic"\n', encoding="utf-8")
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


def test_preflight_does_not_swap_for_one_shot_invocations(monkeypatch, tmp_path):
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
