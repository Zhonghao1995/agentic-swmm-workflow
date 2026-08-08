"""Tests for ``agentic_swmm.providers.factory.make_provider``.

The factory is the single seam every caller migrates onto. Since
ADR-0008 it is route-driven: every supported name is a RouteSpec in
``providers/routes.py`` mapped onto one of three stdlib wire clients.
It must:

- Return the right wire client for each route (openai default,
  anthropic native, chat-wire for the OpenAI-compatible fleet).
- Resolve model and base_url at the seam (env > config > route table).
- Wrap the primary in a FallbackProvider when ``provider.fallback``
  names a different valid route.
- Stay import-light: importing the factory must not import the
  provider modules eagerly (the branches lazy-import).
- Reject unknown providers (including the retired ``claude_sdk``) with
  a clear ValueError.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def isolated_home(monkeypatch, tmp_path):
    """Point config/key resolution at an empty fake home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AISWMM_CONFIG_DIR", raising=False)
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "AISWMM_OPENAI_MODEL",
        "AISWMM_OPENAI_BASE_URL",
        "AISWMM_OPENROUTER_MODEL",
        "AISWMM_OPENROUTER_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    return home


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

    def test_chat_route_returns_chat_provider_with_route_base_url(self, isolated_home):
        from agentic_swmm.providers.factory import make_provider
        from agentic_swmm.providers.openai_chat import OpenAIChatProvider

        provider = make_provider("openrouter", model="openai/gpt-5.5")
        assert isinstance(provider, OpenAIChatProvider)
        assert provider.model == "openai/gpt-5.5"
        assert provider.base_url == "https://openrouter.ai/api/v1"
        assert provider.keyless is False

    def test_keyless_local_route_constructs_without_key(self, isolated_home):
        from agentic_swmm.providers.factory import make_provider
        from agentic_swmm.providers.openai_chat import OpenAIChatProvider

        provider = make_provider("ollama")
        assert isinstance(provider, OpenAIChatProvider)
        assert provider.keyless is True
        assert provider.base_url == "http://localhost:11434/v1"
        assert provider.model == "llama3.1:8b"

    def test_supported_providers_is_the_route_table(self):
        from agentic_swmm.providers.factory import SUPPORTED_PROVIDERS

        assert SUPPORTED_PROVIDERS == (
            "openai",
            "anthropic",
            "codex",
            "openrouter",
            "deepseek",
            "groq",
            "gemini",
            "ollama",
            "lmstudio",
            "custom",
        )
        # The shipped default stays first so help text / menus lead with it.
        assert SUPPORTED_PROVIDERS[0] == "openai"

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

    def test_default_model_resolves_from_route_table_when_none(self, isolated_home):
        """``model=None`` resolves at the seam: env > config > route default.

        With an empty home and no env overrides, the route table's
        default wins, so downstream callers never juggle a None model.
        """
        from agentic_swmm.providers.factory import make_provider

        provider = make_provider("openai", model=None)
        assert provider.model == "gpt-5.5"

    def test_model_env_override_beats_route_default(self, isolated_home, monkeypatch):
        from agentic_swmm.providers.factory import make_provider

        monkeypatch.setenv("AISWMM_OPENAI_MODEL", "gpt-5.5-mini")
        provider = make_provider("openai")
        assert provider.model == "gpt-5.5-mini"

    def test_base_url_env_override_repoints_the_wire(self, isolated_home, monkeypatch):
        """The neutral gateway seam: any OpenAI-compatible endpoint."""
        from agentic_swmm.providers.factory import make_provider

        monkeypatch.setenv("AISWMM_OPENAI_BASE_URL", "http://localhost:8317/v1/")
        provider = make_provider("openai", model="gpt-5.5")
        assert provider.base_url == "http://localhost:8317/v1"
        assert provider._endpoint == "http://localhost:8317/v1/responses"

    def test_config_base_url_override(self, isolated_home):
        from agentic_swmm.providers.factory import make_provider

        aiswmm_dir = isolated_home / ".aiswmm"
        aiswmm_dir.mkdir()
        (aiswmm_dir / "config.toml").write_text(
            '[openrouter]\nbase_url = "https://proxy.example/v1"\nmodel = "meta/llama-4"\n',
            encoding="utf-8",
        )
        provider = make_provider("openrouter")
        assert provider.base_url == "https://proxy.example/v1"
        assert provider.model == "meta/llama-4"

    def test_make_provider_accepts_no_model_kwarg(self, isolated_home):
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
            "agentic_swmm.providers.openai_chat",
            "agentic_swmm.providers.fallback",
        ):
            sys.modules.pop(mod, None)

        import agentic_swmm.providers.factory  # noqa: F401

        # The branches lazy-import inside make_provider, so no wire
        # client module is pulled in by the bare factory import.
        assert "agentic_swmm.providers.anthropic_api" not in sys.modules
        assert "agentic_swmm.providers.openai_api" not in sys.modules
        assert "agentic_swmm.providers.openai_chat" not in sys.modules
        assert "agentic_swmm.providers.fallback" not in sys.modules


class TestFallbackWiring:
    def test_fallback_config_wraps_primary(self, isolated_home):
        from agentic_swmm.providers.factory import make_provider
        from agentic_swmm.providers.fallback import FallbackProvider

        aiswmm_dir = isolated_home / ".aiswmm"
        aiswmm_dir.mkdir()
        (aiswmm_dir / "config.toml").write_text(
            '[provider]\ndefault = "openai"\nfallback = "ollama"\n',
            encoding="utf-8",
        )
        provider = make_provider("openai", model="gpt-5.5")
        assert isinstance(provider, FallbackProvider)

    def test_no_fallback_config_returns_bare_provider(self, isolated_home):
        from agentic_swmm.providers.factory import make_provider
        from agentic_swmm.providers.openai_api import OpenAIProvider

        provider = make_provider("openai", model="gpt-5.5")
        assert isinstance(provider, OpenAIProvider)

    def test_self_fallback_is_ignored(self, isolated_home):
        from agentic_swmm.providers.factory import make_provider
        from agentic_swmm.providers.openai_api import OpenAIProvider

        aiswmm_dir = isolated_home / ".aiswmm"
        aiswmm_dir.mkdir()
        (aiswmm_dir / "config.toml").write_text(
            '[provider]\ndefault = "openai"\nfallback = "openai"\n',
            encoding="utf-8",
        )
        provider = make_provider("openai", model="gpt-5.5")
        assert isinstance(provider, OpenAIProvider)

    def test_unknown_fallback_is_ignored_with_warning(self, isolated_home, capsys):
        from agentic_swmm.providers.factory import make_provider
        from agentic_swmm.providers.openai_api import OpenAIProvider

        aiswmm_dir = isolated_home / ".aiswmm"
        aiswmm_dir.mkdir()
        (aiswmm_dir / "config.toml").write_text(
            '[provider]\ndefault = "openai"\nfallback = "nonsense"\n',
            encoding="utf-8",
        )
        provider = make_provider("openai", model="gpt-5.5")
        assert isinstance(provider, OpenAIProvider)
        assert "nonsense" in capsys.readouterr().err
