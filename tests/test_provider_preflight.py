"""Tests for ``agentic_swmm.agent.provider_preflight`` (PRD-08 A.3)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentic_swmm.agent import provider_preflight


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point ``Path.home()`` at a fresh tmp dir to isolate config files.

    Issue #182 hides ``claude_sdk`` behind the
    ``AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS`` env gate; the tests in
    this module exercise the PRD-09 tier-1 / tier-3 claude_sdk paths
    that are only reachable when the gate is ON, so we set the gate
    here. Gate-OFF behaviour is covered by
    ``tests/test_provider_preflight_gate.py``.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS", "1")
    return home


class TestCheckInteractiveProvider:
    def test_env_var_set(self, isolated_home, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is True
        assert result.provider_name == "openai"
        assert result.fallback_planner == "rule"
        assert result.guidance_message == ""

    def test_env_var_empty_treated_as_unset(self, isolated_home, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "   ")
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is False

    def test_no_key_anywhere(self, isolated_home):
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is False
        assert result.provider_name is None
        assert result.fallback_planner == "rule"
        assert "No LLM provider configured" in result.guidance_message
        assert "export OPENAI_API_KEY" in result.guidance_message
        assert "aiswmm setup --provider openai" in result.guidance_message
        assert "rule-planner mode" in result.guidance_message

    def test_picks_up_env_file(self, isolated_home):
        env_dir = isolated_home / ".aiswmm"
        env_dir.mkdir()
        (env_dir / "env").write_text(
            'export OPENAI_API_KEY="sk-from-env-file"\n', encoding="utf-8"
        )
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is True
        assert result.provider_name == "openai"

    def test_picks_up_config_toml(self, isolated_home):
        cfg_dir = isolated_home / ".aiswmm"
        cfg_dir.mkdir()
        (cfg_dir / "config.toml").write_text(
            '[openai]\napi_key = "sk-from-toml"\n', encoding="utf-8"
        )
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is True

    def test_env_file_with_blank_value_doesnt_count(self, isolated_home):
        env_dir = isolated_home / ".aiswmm"
        env_dir.mkdir()
        (env_dir / "env").write_text(
            'export OPENAI_API_KEY=""\n', encoding="utf-8"
        )
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is False

    def test_guidance_message_format(self, isolated_home):
        result = provider_preflight.check_interactive_provider()
        # Stable multi-line text listing both provider options (PRD-09).
        msg = result.guidance_message
        assert msg.startswith("No LLM provider configured.")
        assert "Quick fix (option 1)" in msg
        assert "Quick fix (option 2)" in msg
        assert "claude login" in msg
        assert msg.endswith(".")


def _write_oauth(home: Path, body: str = '{"token": "x"}') -> None:
    """Drop a Claude Code OAuth credentials file under ``home``."""
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / ".credentials.json").write_text(body, encoding="utf-8")


def _write_config_default(home: Path, provider: str) -> None:
    """Write a ``provider.default`` opt-in into ``~/.aiswmm/config.toml``."""
    cfg_dir = home / ".aiswmm"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(
        f'[provider]\ndefault = "{provider}"\n', encoding="utf-8"
    )


class TestClaudeOAuthDetection:
    """PRD-09 §5.3 — the fourth (Claude subscription) preflight tier."""

    def test_oauth_file_present_and_optin_selects_claude_sdk(self, isolated_home):
        _write_oauth(isolated_home)
        _write_config_default(isolated_home, "claude_sdk")
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is True
        assert result.provider_name == "claude_sdk"

    def test_oauth_present_no_optin_still_openai_when_key_set(
        self, isolated_home, monkeypatch
    ):
        # OAuth file exists but the user never opted in; an OpenAI key
        # is set, so the OpenAI tier still wins.
        _write_oauth(isolated_home)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "openai"

    def test_openai_key_plus_oauth_plus_explicit_optin_picks_claude_sdk(
        self, isolated_home, monkeypatch
    ):
        # Explicit ``provider.default = claude_sdk`` wins even when an
        # OpenAI key is also present.
        _write_oauth(isolated_home)
        _write_config_default(isolated_home, "claude_sdk")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "claude_sdk"

    def test_oauth_present_no_optin_no_openai_surfaces_claude_sdk(
        self, isolated_home
    ):
        # A bare OAuth file with no OpenAI key: surface claude_sdk as
        # available so the runtime does not drop to the rule planner.
        _write_oauth(isolated_home)
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is True
        assert result.provider_name == "claude_sdk"

    def test_neither_provider_lists_both_options(self, isolated_home):
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is False
        assert "OPENAI_API_KEY" in result.guidance_message
        assert "claude login" in result.guidance_message

    def test_malformed_oauth_file_treated_as_absent(self, isolated_home):
        # An empty / non-JSON credentials file must not crash the
        # preflight and must not count as a configured provider.
        _write_oauth(isolated_home, body="")
        assert provider_preflight.detect_claude_oauth() is False
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is False

    def test_non_json_oauth_file_treated_as_absent(self, isolated_home):
        _write_oauth(isolated_home, body="not json at all {{{")
        assert provider_preflight.detect_claude_oauth() is False
