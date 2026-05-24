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

    @pytest.mark.parametrize(
        "value",
        ["1", "true", "TRUE", "True", "yes", "YES", "Yes", "on", "ON", "On"],
    )
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


class TestSharedTruthyHelper:
    """Issue #191: gate predicate consumes the shared truthy helper.

    ``experimental_providers`` must not maintain its own ``_TRUTHY``
    membership set. The truthy-string contract lives in
    ``agentic_swmm.agent.feature_flags`` so a single edit propagates
    to every ``AISWMM_*`` boolean env var.
    """

    def test_module_does_not_define_local_truthy_set(self):
        # Module attribute is the contract: no local ``_TRUTHY`` set
        # to drift out of sync with ``feature_flags``.
        assert not hasattr(experimental_providers, "_TRUTHY")

    def test_claude_sdk_enabled_delegates_to_feature_flags_helper(
        self, monkeypatch
    ):
        # Patch the shared helper at its definition site and observe
        # that ``claude_sdk_enabled`` routes through it. If the gate
        # ever reverts to a local lookup, the patched stub would be
        # bypassed and this test would fail.
        from agentic_swmm.agent import feature_flags

        calls: list[str | None] = []

        def _spy(value: str | None) -> bool:
            calls.append(value)
            return True

        monkeypatch.setattr(feature_flags, "is_truthy", _spy, raising=True)
        monkeypatch.setenv(_ENV_VAR, "anything-the-spy-returns-true")
        assert experimental_providers.claude_sdk_enabled() is True
        assert calls, "claude_sdk_enabled did not consult feature_flags.is_truthy"


class TestPublicIsTruthyHelper:
    """Issue #191: promote the truthy helper to a public surface."""

    def test_feature_flags_exposes_public_is_truthy(self):
        from agentic_swmm.agent import feature_flags

        assert callable(getattr(feature_flags, "is_truthy", None))

    def test_public_helper_accepts_all_truthy_values(self):
        from agentic_swmm.agent import feature_flags

        for value in ("1", "true", "TRUE", "yes", "Yes", "on", "ON"):
            assert feature_flags.is_truthy(value) is True, value

    def test_public_helper_rejects_non_truthy_values(self):
        from agentic_swmm.agent import feature_flags

        for value in (None, "", "0", "false", "no", "off", "maybe", "  "):
            assert feature_flags.is_truthy(value) is False, value
