"""Tests for ``agentic_swmm.agent.provider_preflight`` (subscription-first).

The preflight resolves which LLM provider the interactive shell should
use. The shipped default is the ``claude_sdk`` subscription path, so:

* A logged-in subscription user (OAuth file, macOS Keychain item, or
  ``ANTHROPIC_API_KEY``) selects ``claude_sdk`` with no guidance.
* OpenAI is opt-in: explicit ``provider.default = openai``, or an
  ``OPENAI_API_KEY`` present with no competing subscription.
* With no credentials at all the preflight still selects ``claude_sdk``
  (the SDK authenticates at call time) but attaches a soft warning.

``isolated_home`` (``tests/conftest.py``) gives each test an empty
``HOME``, clears ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``, and
neutralises the macOS Keychain probe to ``False`` so the slate is known.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentic_swmm.agent import provider_preflight


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


def _write_openai_env(home: Path, value: str = "sk-from-env-file") -> None:
    env_dir = home / ".aiswmm"
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "env").write_text(
        f'export OPENAI_API_KEY="{value}"\n', encoding="utf-8"
    )


class TestSubscriptionFirstSelection:
    """The default resolves to the claude_sdk subscription path."""

    def test_no_credentials_still_selects_claude_sdk_with_warning(
        self, isolated_home
    ):
        # Empty slate: shipped default is claude_sdk. A logged-in macOS
        # user must not drop to rule, so we select claude_sdk even with
        # no detectable credentials, and surface a soft warning.
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is True
        assert result.provider_name == "claude_sdk"
        assert result.fallback_planner == "rule"
        assert result.guidance_message  # soft warning present
        assert "aiswmm login" in result.guidance_message

    def test_oauth_file_present_selects_claude_sdk_no_guidance(self, isolated_home):
        _write_oauth(isolated_home)
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "claude_sdk"
        assert result.guidance_message == ""

    def test_anthropic_api_key_counts_as_subscription_signal(
        self, isolated_home, monkeypatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "claude_sdk"
        assert result.guidance_message == ""

    def test_explicit_claude_sdk_optin_selects_claude_sdk(self, isolated_home):
        _write_config_default(isolated_home, "claude_sdk")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "claude_sdk"


class TestOpenAIOptIn:
    """OpenAI is reachable only as an explicit / no-subscription opt-in."""

    def test_explicit_openai_default_selects_openai(self, isolated_home):
        _write_config_default(isolated_home, "openai")
        result = provider_preflight.check_interactive_provider()
        assert result.has_configured_provider is True
        assert result.provider_name == "openai"
        assert result.guidance_message == ""

    def test_openai_env_key_with_no_subscription_selects_openai(
        self, isolated_home, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "openai"

    def test_openai_env_file_with_no_subscription_selects_openai(
        self, isolated_home
    ):
        _write_openai_env(isolated_home)
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "openai"

    def test_openai_config_key_with_no_subscription_selects_openai(
        self, isolated_home
    ):
        cfg_dir = isolated_home / ".aiswmm"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.toml").write_text(
            '[openai]\napi_key = "sk-from-toml"\n', encoding="utf-8"
        )
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "openai"

    def test_blank_openai_env_value_does_not_select_openai(self, isolated_home):
        env_dir = isolated_home / ".aiswmm"
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "env").write_text(
            'export OPENAI_API_KEY=""\n', encoding="utf-8"
        )
        result = provider_preflight.check_interactive_provider()
        # Falls through to the subscription default.
        assert result.provider_name == "claude_sdk"

    def test_subscription_wins_over_lingering_openai_key(
        self, isolated_home, monkeypatch
    ):
        # A logged-in subscription user with a stale OpenAI key keeps the
        # subscription — never silently routed to paid OpenAI.
        _write_oauth(isolated_home)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "claude_sdk"

    def test_explicit_openai_wins_even_with_subscription(
        self, isolated_home, monkeypatch
    ):
        # If the user explicitly pinned openai, honour it even if a
        # subscription credential is also present.
        _write_oauth(isolated_home)
        _write_config_default(isolated_home, "openai")
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "openai"


class TestMacOSKeychainDetection:
    """The macOS Keychain probe is a positive subscription signal."""

    def test_keychain_hit_counts_as_subscription(self, isolated_home, monkeypatch):
        # Re-enable the probe (isolated_home neutralises it) and make it
        # report a hit.
        monkeypatch.setattr(
            provider_preflight,
            "_detect_macos_keychain_credentials",
            lambda: True,
            raising=True,
        )
        assert provider_preflight.detect_claude_oauth() is True
        result = provider_preflight.check_interactive_provider()
        assert result.provider_name == "claude_sdk"
        assert result.guidance_message == ""

    def test_keychain_probe_inspects_returncode_only_never_w_flag(
        self, monkeypatch
    ):
        # Security contract: the probe must call ``security
        # find-generic-password -s "Claude Code-credentials"`` WITHOUT
        # ``-w`` (which would print the secret) and decide on the
        # returncode alone.
        monkeypatch.setattr(provider_preflight.sys, "platform", "darwin")
        captured: dict = {}

        class _Completed:
            returncode = 0

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _Completed()

        monkeypatch.setattr(provider_preflight.subprocess, "run", _fake_run)
        assert provider_preflight._detect_macos_keychain_credentials() is True
        assert captured["cmd"][:2] == ["security", "find-generic-password"]
        assert "-s" in captured["cmd"]
        assert "Claude Code-credentials" in captured["cmd"]
        assert "-w" not in captured["cmd"], "must never request the secret value"

    def test_keychain_nonzero_returncode_is_absent(self, monkeypatch):
        monkeypatch.setattr(provider_preflight.sys, "platform", "darwin")

        class _Completed:
            returncode = 44

        monkeypatch.setattr(
            provider_preflight.subprocess, "run", lambda cmd, **kw: _Completed()
        )
        assert provider_preflight._detect_macos_keychain_credentials() is False

    def test_keychain_probe_skipped_off_darwin(self, monkeypatch):
        monkeypatch.setattr(provider_preflight.sys, "platform", "linux")

        def _should_not_run(cmd, **kwargs):  # pragma: no cover - must not fire
            raise AssertionError("security must not be invoked off macOS")

        monkeypatch.setattr(provider_preflight.subprocess, "run", _should_not_run)
        assert provider_preflight._detect_macos_keychain_credentials() is False

    def test_keychain_probe_swallows_subprocess_failure(self, monkeypatch):
        monkeypatch.setattr(provider_preflight.sys, "platform", "darwin")

        def _boom(cmd, **kwargs):
            raise OSError("security not found")

        monkeypatch.setattr(provider_preflight.subprocess, "run", _boom)
        assert provider_preflight._detect_macos_keychain_credentials() is False


class TestOAuthFileDetection:
    def test_malformed_oauth_file_treated_as_absent(self, isolated_home):
        _write_oauth(isolated_home, body="")
        assert provider_preflight.detect_claude_oauth() is False

    def test_non_json_oauth_file_treated_as_absent(self, isolated_home):
        _write_oauth(isolated_home, body="not json at all {{{")
        assert provider_preflight.detect_claude_oauth() is False


class TestGuidanceBanner:
    """The soft-warning banner leads with the subscription path."""

    def test_no_credentials_banner_leads_with_subscription(self, isolated_home):
        msg = provider_preflight.check_interactive_provider().guidance_message
        assert msg
        # Subscription path is named first; OpenAI is the alternative.
        assert "aiswmm login" in msg
        sub_index = msg.find("aiswmm login")
        openai_index = msg.find("OPENAI_API_KEY")
        assert sub_index < openai_index, "subscription path must lead the banner"
        assert "claude_sdk" in msg
