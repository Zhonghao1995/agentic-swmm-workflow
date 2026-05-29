"""Tests for ``agentic_swmm.providers.factory.make_provider``.

The factory is the single seam every caller migrates onto. It must:

- Return the right provider class for each supported name
  (``openai`` default, ``anthropic`` opt-in).
- Stay import-light: importing the factory must not import the
  anthropic / openai provider modules eagerly (the branches lazy-import).
- Reject unknown providers (including the retired ``claude_sdk``) with
  a clear ValueError.
"""
from __future__ import annotations

import sys

import pytest


class TestMakeProvider:
    def test_openai_returns_openai_provider(self):
        from agentic_swmm.providers.factory import make_provider
        from agentic_swmm.providers.openai_api import OpenAIProvider

        provider = make_provider("openai", model="gpt-5.5")
        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "gpt-5.5"

    def test_anthropic_returns_anthropic_provider(self):
        from agentic_swmm.providers.factory import make_provider
        from agentic_swmm.providers.anthropic_api import AnthropicProvider

        provider = make_provider("anthropic", model="claude-sonnet-4-6")
        assert isinstance(provider, AnthropicProvider)
        assert provider.model == "claude-sonnet-4-6"

    def test_supported_providers_is_openai_and_anthropic(self):
        from agentic_swmm.providers.factory import SUPPORTED_PROVIDERS

        assert SUPPORTED_PROVIDERS == ("openai", "anthropic")

    def test_unknown_provider_raises_value_error(self):
        from agentic_swmm.providers.factory import make_provider

        with pytest.raises(ValueError) as exc_info:
            make_provider("nonsense", model="x")
        assert "unsupported provider" in str(exc_info.value).lower()
        assert "nonsense" in str(exc_info.value)

    def test_retired_claude_sdk_now_raises_value_error(self):
        # The subscription backend was removed; ``claude_sdk`` must be
        # rejected like any other unknown provider, not built.
        from agentic_swmm.providers.factory import make_provider

        with pytest.raises(ValueError) as exc_info:
            make_provider("claude_sdk", model="x")
        assert "unsupported provider" in str(exc_info.value).lower()
        assert "claude_sdk" in str(exc_info.value)

    def test_default_model_resolves_from_config_when_none(self):
        """``model=None`` must not crash — the provider keeps None and the
        downstream caller decides (the constructor would error later at
        call time, not at construction)."""
        from agentic_swmm.providers.factory import make_provider

        provider = make_provider("openai", model=None)
        assert getattr(provider, "model", "sentinel") is None or provider.model == ""

    def test_make_provider_accepts_no_model_kwarg(self):
        from agentic_swmm.providers.factory import make_provider

        provider = make_provider("openai")
        assert provider is not None

    def test_factory_module_import_is_light(self):
        """Importing the factory must not eagerly import the provider
        modules — keeps ``--provider`` argparse wiring import-cheap."""
        for mod in (
            "agentic_swmm.providers.factory",
            "agentic_swmm.providers.anthropic_api",
            "agentic_swmm.providers.openai_api",
        ):
            sys.modules.pop(mod, None)

        import agentic_swmm.providers.factory  # noqa: F401

        # The branches lazy-import inside make_provider, so neither
        # provider module is pulled in by the bare factory import.
        assert "agentic_swmm.providers.anthropic_api" not in sys.modules
        assert "agentic_swmm.providers.openai_api" not in sys.modules
