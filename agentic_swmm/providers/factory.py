"""Provider construction seam.

aiswmm historically hard-coded ``OpenAIProvider(model=...)`` in five
call sites. This factory is the single seam every caller migrates onto,
so the rest of aiswmm only ever sees the ``ChatProvider`` Protocol from
:mod:`agentic_swmm.providers.base`.

Two API-key backends are supported, both standard function-calling:

* ``"openai"`` — the default. OpenAI Responses API
  (:class:`~agentic_swmm.providers.openai_api.OpenAIProvider`), needs
  ``OPENAI_API_KEY``.
* ``"anthropic"`` — opt-in (``--provider anthropic``). Native Anthropic
  Messages API
  (:class:`~agentic_swmm.providers.anthropic_api.AnthropicProvider`),
  needs ``ANTHROPIC_API_KEY``.

Both providers are pure-stdlib (``urllib``) and ship in the core
package — there is no optional extra and no subprocess/SDK. Adding a
third backend later is a branch here plus a
:data:`SUPPORTED_PROVIDERS` entry; nothing else changes.
"""
from __future__ import annotations

from agentic_swmm.providers.base import ChatProvider


SUPPORTED_PROVIDERS = ("openai", "anthropic")
"""Canonical tuple of provider names this factory accepts.

Single source of truth for the set so every ``--provider`` argparse
site (and any future caller) derives its choices from one tuple instead
of restating the literal.
"""


def make_provider(provider_name: str, *, model: str | None = None) -> ChatProvider:
    """Return a :class:`ChatProvider` for the requested backend name.

    Supported names: ``"openai"`` (default) and ``"anthropic"``. Both
    are pure-Python ``urllib`` paths.

    The API key is resolved here, at the one construction seam, using the
    same resolver ``doctor`` and ``login --status`` report from. That
    shared resolver is the point: when the providers read only
    ``os.environ`` while the diagnostics also read ``~/.aiswmm/env``, a
    user who had completed `aiswmm login` was told the key was present
    and then handed "<VAR> is not set" by the runtime.

    Raises:
        ValueError: when ``provider_name`` is not in
            :data:`SUPPORTED_PROVIDERS`.
    """
    from agentic_swmm.agent.provider_preflight import provider_key_value

    api_key = provider_key_value(provider_name)
    if provider_name == "openai":
        from agentic_swmm.providers.openai_api import OpenAIProvider

        return OpenAIProvider(model=model, api_key=api_key)  # type: ignore[arg-type]
    if provider_name == "anthropic":
        from agentic_swmm.providers.anthropic_api import AnthropicProvider

        return AnthropicProvider(model=model, api_key=api_key)  # type: ignore[arg-type]
    raise ValueError(
        f"unsupported provider: {provider_name!r}. "
        f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}."
    )


__all__ = ["SUPPORTED_PROVIDERS", "make_provider"]
