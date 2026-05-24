"""Argparse-level enforcement of the ``claude_sdk`` env gate (issue #182).

When ``AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS`` is unset, every
``--provider`` flag across ``agent`` / ``chat`` / ``model`` / ``setup``
must reject ``claude_sdk`` at parse time with an ``invalid choice``
SystemExit.

The gate-ON path is already covered by
``tests/test_cli_provider_choices.py``; this module adds the
gate-OFF regression so the four argparse sites cannot drift out of
sync with the helper.
"""
from __future__ import annotations

import argparse
import io
from contextlib import redirect_stderr

import pytest

from agentic_swmm.commands import (
    agent as agent_cmd,
    chat as chat_cmd,
    model as model_cmd,
    setup as setup_cmd,
)


_ENV_VAR = "AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS"


def _register_one(register_fn) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_fn(sub)
    return parser


def _assert_rejects_claude_sdk(parser: argparse.ArgumentParser, argv: list[str]) -> None:
    err = io.StringIO()
    with redirect_stderr(err), pytest.raises(SystemExit):
        parser.parse_args(argv)
    assert "invalid choice" in err.getvalue()
    assert "claude_sdk" in err.getvalue()


class TestArgparseGateOff:
    @pytest.fixture(autouse=True)
    def _gate_off(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)

    def test_agent_rejects_claude_sdk(self) -> None:
        parser = _register_one(agent_cmd.register)
        _assert_rejects_claude_sdk(
            parser, ["agent", "--provider", "claude_sdk", "hi"]
        )

    def test_chat_rejects_claude_sdk(self) -> None:
        parser = _register_one(chat_cmd.register)
        _assert_rejects_claude_sdk(parser, ["chat", "--provider", "claude_sdk"])

    def test_model_rejects_claude_sdk(self) -> None:
        parser = _register_one(model_cmd.register)
        _assert_rejects_claude_sdk(parser, ["model", "--provider", "claude_sdk"])

    def test_setup_rejects_claude_sdk(self) -> None:
        parser = _register_one(setup_cmd.register)
        _assert_rejects_claude_sdk(parser, ["setup", "--provider", "claude_sdk"])

    def test_agent_still_accepts_openai(self) -> None:
        parser = _register_one(agent_cmd.register)
        args = parser.parse_args(["agent", "--provider", "openai", "hi"])
        assert args.provider == "openai"

    def test_setup_help_text_omits_claude(self, capsys) -> None:
        # The PRD states the dynamic help text must not mention
        # ``claude_sdk`` or "Claude Pro/Max" when the gate is OFF.
        parser = _register_one(setup_cmd.register)
        with pytest.raises(SystemExit):
            parser.parse_args(["setup", "--help"])
        captured = capsys.readouterr().out
        assert "claude_sdk" not in captured
        assert "Claude Pro/Max" not in captured
