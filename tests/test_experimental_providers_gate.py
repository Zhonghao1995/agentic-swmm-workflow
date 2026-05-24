"""Unit tests for the ``claude_sdk`` env gate (issue #182).

The gate is the single predicate every user-facing surface consults
before exposing the ``claude_sdk`` provider. When the env var
``AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS`` is unset (or set to a non-
truthy value), ``claude_sdk`` must be invisible in argparse choices,
absent from the welcome banner, and silently downgraded to ``openai``
at runtime.

This module pins the helper API (`claude_sdk_enabled`,
`available_provider_choices`, `gate_notice_for_legacy_config`) so
that subsequent surface-level commits can lean on a stable contract.
"""
from __future__ import annotations

import pytest

from agentic_swmm.agent import experimental_providers


_ENV_VAR = "AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS"


class TestClaudeSdkEnabled:
    def test_unset_returns_false(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        assert experimental_providers.claude_sdk_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "YES", "Yes"])
    def test_truthy_values_return_true(self, monkeypatch, value):
        monkeypatch.setenv(_ENV_VAR, value)
        assert experimental_providers.claude_sdk_enabled() is True

    @pytest.mark.parametrize(
        "value",
        ["", "0", "false", "FALSE", "no", "NO", "off", "anything-else"],
    )
    def test_non_truthy_values_return_false(self, monkeypatch, value):
        monkeypatch.setenv(_ENV_VAR, value)
        assert experimental_providers.claude_sdk_enabled() is False


class TestAvailableProviderChoices:
    def test_gate_off_returns_openai_only(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        assert experimental_providers.available_provider_choices() == ["openai"]

    def test_gate_on_returns_both(self, monkeypatch):
        monkeypatch.setenv(_ENV_VAR, "1")
        assert experimental_providers.available_provider_choices() == [
            "openai",
            "claude_sdk",
        ]


class TestGateNoticeForLegacyConfig:
    def test_notice_mentions_env_var(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        notice = experimental_providers.gate_notice_for_legacy_config()
        assert "AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS" in notice

    def test_notice_mentions_config_set_command(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        notice = experimental_providers.gate_notice_for_legacy_config()
        assert "aiswmm config set provider.default openai" in notice

    def test_notice_is_non_empty_string(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        notice = experimental_providers.gate_notice_for_legacy_config()
        assert isinstance(notice, str)
        assert notice.strip()
