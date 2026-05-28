"""Tests for ``agentic_swmm.providers.factory.make_provider`` (PRD-09).

The factory is the single seam every caller migrates onto. It must:

- Return the right provider class for each registered name.
- Lazy-import the optional Claude Agent SDK so users who never enable
  the extra do not pay any import cost.
- Surface an actionable error when the extra is not installed.
- Reject unknown providers with a clear message.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest


class TestMakeProvider:
    def test_openai_returns_openai_provider(self):
        from agentic_swmm.providers.factory import make_provider
        from agentic_swmm.providers.openai_api import OpenAIProvider

        provider = make_provider("openai", model="gpt-5.5")
        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "gpt-5.5"

    def test_claude_sdk_returns_claude_sdk_provider(self, mock_claude_sdk_module):
        from agentic_swmm.providers.factory import make_provider
        from agentic_swmm.providers.claude_sdk_api import ClaudeSDKProvider

        provider = make_provider("claude_sdk", model="claude-sonnet-4-5-20250929")
        assert isinstance(provider, ClaudeSDKProvider)
        assert provider.model == "claude-sonnet-4-5-20250929"

    def test_unknown_provider_raises_value_error(self):
        from agentic_swmm.providers.factory import make_provider

        with pytest.raises(ValueError) as exc_info:
            make_provider("nonsense", model="x")
        assert "unsupported provider" in str(exc_info.value).lower()
        assert "nonsense" in str(exc_info.value)

    def test_claude_sdk_without_extra_raises_actionable_runtime_error(self, monkeypatch):
        """When the optional extra is uninstalled, the factory must
        raise a RuntimeError pointing at the pip extra command — not a
        bare ImportError that would leak the SDK module name.

        We simulate the missing extra by inserting a stub module that
        raises ``ImportError`` on import of the provider submodule.
        That stub is read by the factory's lazy ``from
        agentic_swmm.providers.claude_sdk_api import ClaudeSDKProvider``
        line.
        """

        # Drop the cached provider module + SDK so the factory must
        # re-import them. Insert ``claude_agent_sdk`` as a sentinel
        # module that raises on attribute access — the provider module
        # under it does ``import claude_agent_sdk`` at runtime in
        # ``_load_sdk``, but the factory itself only imports the
        # provider module. So we need to make the *provider module*
        # import fail.
        sys.modules.pop("agentic_swmm.providers.claude_sdk_api", None)
        sys.modules.pop("claude_agent_sdk", None)

        # Install a fake provider module that raises ImportError when
        # imported — the factory will then map it to the actionable
        # RuntimeError.
        broken = types.ModuleType("agentic_swmm.providers.claude_sdk_api")

        class _Sentinel:
            def __getattr__(self, item):
                raise ImportError("claude_agent_sdk is required for the claude_sdk provider")

        # Inject a meta-path hook that fails the provider import.
        class _BrokenFinder:
            def find_spec(self, name, path, target=None):
                if name == "agentic_swmm.providers.claude_sdk_api":
                    # Returning a broken spec that raises on load.
                    import importlib.machinery as _m

                    class _BrokenLoader:
                        def create_module(self, spec):
                            return None

                        def exec_module(self, module):
                            raise ImportError(
                                "claude_agent_sdk is required for the claude_sdk provider"
                            )

                    return _m.ModuleSpec(name, _BrokenLoader())
                return None

        monkeypatch.setattr(sys, "meta_path", [_BrokenFinder()] + sys.meta_path)

        # Re-import the factory so its function-body lazy import picks
        # up the broken loader.
        sys.modules.pop("agentic_swmm.providers.factory", None)
        from agentic_swmm.providers.factory import make_provider

        with pytest.raises(RuntimeError) as exc_info:
            make_provider("claude_sdk", model="x")

        msg = str(exc_info.value)
        assert "pip install aiswmm[claude]" in msg
        assert "claude_sdk" in msg or "Claude" in msg

    def test_factory_module_import_does_not_load_claude_sdk(self):
        """Importing the factory must not import claude_agent_sdk —
        keeps the OpenAI-only path import-light."""
        sys.modules.pop("agentic_swmm.providers.factory", None)
        sys.modules.pop("agentic_swmm.providers.claude_sdk_api", None)
        sys.modules.pop("claude_agent_sdk", None)

        import agentic_swmm.providers.factory  # noqa: F401

        assert "claude_agent_sdk" not in sys.modules

    def test_default_model_resolves_from_config_when_none(self, monkeypatch, tmp_path):
        """``model=None`` must not crash — the factory either supplies
        a default or delegates to the provider. For OpenAI the
        constructor accepts ``model=None`` (it would error later at
        ``.complete()`` rather than at construction); we mirror that
        contract here."""
        from agentic_swmm.providers.factory import make_provider

        provider = make_provider("openai", model=None)
        # The provider keeps None; downstream caller decides.
        assert getattr(provider, "model", "sentinel") is None or provider.model == ""

    def test_make_provider_accepts_no_model_kwarg(self):
        from agentic_swmm.providers.factory import make_provider

        provider = make_provider("openai")
        assert provider is not None
