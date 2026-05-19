"""Tests for ``agentic_swmm.agent.provider_preflight`` (PRD-08 A.3)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentic_swmm.agent import provider_preflight


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point ``Path.home()`` at a fresh tmp dir to isolate config files."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
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
        assert "OpenAI API key not configured" in result.guidance_message
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
        # 5 distinct blocks: header, blank, fix header, fix commands, blank, footer.
        # The format_for_stderr-like contract: stable multi-line text.
        msg = result.guidance_message
        assert msg.startswith("OpenAI API key not configured.")
        assert "Quick fix:" in msg
        assert msg.endswith(".")
