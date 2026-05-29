"""Tests for ``agentic_swmm.agent.provider_preflight`` (two API keys).

The preflight resolves which LLM provider the interactive shell should
use from the two API-key backends. The shipped default is ``openai``:

* The resolved default is ``provider.default`` (config) when set, else
  ``openai``.
* A provider is *configured* when its key is reachable (env var,
  ``~/.aiswmm/env``, or the ``[<provider>]`` config section).
* The selected provider is kept even with no detectable key (it
  authenticates at call time) but a soft ``aiswmm login`` warning is
  attached.

``isolated_home`` (``tests/conftest.py``) gives each test an empty
``HOME`` and clears ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` so the
slate is known.
"""
from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent import provider_preflight


def _write_config_default(home: Path, provider: str) -> None:
    """Write a ``provider.default`` opt-in into ``~/.aiswmm/config.toml``."""
    cfg_dir = home / ".aiswmm"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(
        f'[provider]\ndefault = "{provider}"\n', encoding="utf-8"
    )


def _write_env_key(home: Path, var_name: str, value: str = "sk-from-env-file") -> None:
    env_dir = home / ".aiswmm"
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "env").write_text(f'export {var_name}="{value}"\n', encoding="utf-8")


class TestDefaultSelection:
    """The shipped default resolves to openai."""

    def test_no_credentials_still_selects_openai_with_warning(self, isolated_home):
        # Empty slate: shipped default is openai. The provider is kept
        # (it authenticates at call time) with a soft warning.
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is True
        assert result.provider_name == "openai"
        assert result.fallback_planner == "rule"
        assert result.guidance_message  # soft warning present
        assert "aiswmm login" in result.guidance_message

    def test_openai_key_present_selects_openai_no_guidance(self, isolated_home, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "openai"
        assert result.guidance_message == ""

    def test_explicit_openai_default_selects_openai(self, isolated_home):
        _write_config_default(isolated_home, "openai")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "openai"


class TestAnthropicOptIn:
    """Anthropic is reachable as an explicit opt-in via provider.default."""

    def test_explicit_anthropic_default_selects_anthropic(self, isolated_home):
        _write_config_default(isolated_home, "anthropic")
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is True
        assert result.provider_name == "anthropic"
        # No key present -> soft warning.
        assert result.guidance_message
        assert "aiswmm login" in result.guidance_message

    def test_anthropic_default_with_key_no_guidance(self, isolated_home, monkeypatch):
        _write_config_default(isolated_home, "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "anthropic"
        assert result.guidance_message == ""

    def test_anthropic_default_with_env_file_key_no_guidance(self, isolated_home):
        _write_config_default(isolated_home, "anthropic")
        _write_env_key(isolated_home, "ANTHROPIC_API_KEY", "sk-ant-file")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "anthropic"
        assert result.guidance_message == ""

    def test_openai_key_does_not_satisfy_anthropic_default(self, isolated_home, monkeypatch):
        # An OpenAI key present while anthropic is the default must NOT
        # suppress the warning — the selected provider's own key is what
        # counts.
        _write_config_default(isolated_home, "anthropic")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "anthropic"
        assert result.guidance_message  # anthropic key still absent


class TestKeyDetectionTiers:
    def test_blank_env_value_does_not_count(self, isolated_home):
        _write_env_key(isolated_home, "OPENAI_API_KEY", "")
        result = provider_preflight.check_interactive_provider()
        # openai default selected but key not detected -> warning.
        assert result.provider_name == "openai"
        assert result.guidance_message

    def test_config_section_api_key_counts(self, isolated_home):
        cfg_dir = isolated_home / ".aiswmm"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.toml").write_text(
            '[openai]\napi_key = "sk-from-toml"\n', encoding="utf-8"
        )
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "openai"
        assert result.guidance_message == ""

    def test_provider_key_present_helper(self, isolated_home, monkeypatch):
        assert provider_preflight.provider_key_present("openai") is False
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        assert provider_preflight.provider_key_present("anthropic") is True
        assert provider_preflight.provider_key_present("openai") is False

    def test_unknown_provider_key_absent(self, isolated_home):
        assert provider_preflight.provider_key_present("nonsense") is False


class TestUnknownDefaultSafetyNet:
    def test_unknown_default_falls_back_to_rule_with_full_guidance(self, isolated_home):
        # A stale/legacy provider name (e.g. the retired claude_sdk) is
        # not a known provider, so the preflight drops to the rule
        # planner and surfaces the full no-provider block.
        _write_config_default(isolated_home, "claude_sdk")
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is False
        assert result.provider_name is None
        assert result.fallback_planner == "rule"
        assert "rule-planner" in result.guidance_message


class TestGuidanceBanner:
    def test_no_credentials_banner_names_both_providers(self, isolated_home):
        msg = provider_preflight.check_interactive_provider().guidance_message
        assert msg
        assert "OPENAI_API_KEY" in msg
        assert "ANTHROPIC_API_KEY" in msg
        assert "aiswmm login" in msg
