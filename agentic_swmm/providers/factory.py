"""Provider construction seam.

aiswmm historically hard-coded ``OpenAIProvider(model=...)`` in five
call sites. This factory is the single seam every caller migrates onto,
so the rest of aiswmm only ever sees the ``ChatProvider`` Protocol from
:mod:`agentic_swmm.providers.base`.

Since ADR-0008 the factory is route-driven: every supported name is a
:class:`~agentic_swmm.providers.routes.RouteSpec` in the route table,
which maps it onto one of three pure-stdlib wire clients:

* ``openai-responses`` — :class:`~agentic_swmm.providers.openai_api.OpenAIProvider`
  (routes: ``openai``, ``codex``).
* ``openai-chat`` — :class:`~agentic_swmm.providers.openai_chat.OpenAIChatProvider`
  (routes: ``openrouter``, ``deepseek``, ``groq``, ``gemini``,
  ``ollama``, ``lmstudio``, ``custom``).
* ``anthropic-messages`` — :class:`~agentic_swmm.providers.anthropic_api.AnthropicProvider`
  (route: ``anthropic``).

Adding a provider that speaks one of these wires is one RouteSpec entry;
nothing changes here. The factory also resolves, at this one seam: the
API key (same three-tier resolver ``doctor`` and ``login --status``
report from), the effective base URL and model (env > config > table
default), and the optional fallback chain (``provider.fallback``).
"""
from __future__ import annotations

from agentic_swmm.providers.base import ChatProvider
from agentic_swmm.providers.routes import (
    ROUTES,
    WIRE_ANTHROPIC_MESSAGES,
    WIRE_OPENAI_RESPONSES,
    RouteSpec,
    resolved_base_url,
    resolved_model,
    route_names,
)


SUPPORTED_PROVIDERS = route_names()
"""Canonical tuple of route names this factory accepts.

Single source of truth for the set so every ``--provider`` argparse
site (and any future caller) derives its choices from one tuple instead
of restating the literal. Derived from the route table (ADR-0008).
"""


def make_provider(provider_name: str, *, model: str | None = None) -> ChatProvider:
    """Return a :class:`ChatProvider` for the requested route name.

    Resolution happens here, at the one construction seam: the API key
    via the shared three-tier resolver (environment > ``~/.aiswmm/env``
    > config section), the base URL and model via the route helpers
    (environment > config section > route default). When
    ``provider.fallback`` names a different valid route, the primary is
    wrapped in a :class:`~agentic_swmm.providers.fallback.FallbackProvider`.

    Raises:
        ValueError: when ``provider_name`` is not a known route.
    """
    if provider_name not in ROUTES:
        raise ValueError(
            f"unsupported provider: {provider_name!r}. "
            f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}."
        )
    from agentic_swmm.config import load_config

    config_get = load_config().get
    primary = _make_single(provider_name, model=model, config_get=config_get)

    fallback_name = str(config_get("provider.fallback", "") or "").strip()
    if not fallback_name or fallback_name == provider_name:
        return primary
    if fallback_name not in ROUTES:
        import sys

        print(
            f"warning: provider.fallback = {fallback_name!r} is not a known route; "
            f"ignoring it. Known routes: {', '.join(SUPPORTED_PROVIDERS)}.",
            file=sys.stderr,
        )
        return primary
    from agentic_swmm.providers.fallback import FallbackProvider

    return FallbackProvider(
        primary=primary,
        fallback=_make_single(fallback_name, model=None, config_get=config_get),
        primary_name=provider_name,
        fallback_name=fallback_name,
    )


def _make_single(provider_name: str, *, model: str | None, config_get) -> ChatProvider:
    """Build the wire client for one route (no fallback wrapping)."""
    spec: RouteSpec = ROUTES[provider_name]
    from agentic_swmm.agent.provider_preflight import provider_key_value

    api_key = provider_key_value(provider_name)
    effective_model = model or resolved_model(provider_name, config_get) or None
    base_url = resolved_base_url(provider_name, config_get)

    if spec.wire == WIRE_ANTHROPIC_MESSAGES:
        from agentic_swmm.providers.anthropic_api import AnthropicProvider

        return AnthropicProvider(
            model=effective_model,  # type: ignore[arg-type]
            api_key=api_key,
            base_url=base_url or None,
        )
    if spec.wire == WIRE_OPENAI_RESPONSES:
        from agentic_swmm.providers.openai_api import OpenAIProvider

        return OpenAIProvider(
            model=effective_model,  # type: ignore[arg-type]
            api_key=api_key,
            base_url=base_url or None,
        )
    from agentic_swmm.providers.openai_chat import OpenAIChatProvider

    return OpenAIChatProvider(
        model=effective_model or "",
        base_url=base_url,
        api_key=api_key,
        keyless=spec.keyless,
        label=spec.label,
        missing_key_error=_missing_key_message(spec),
        auth_hint=_auth_hint(spec),
    )


def _missing_key_message(spec: RouteSpec) -> str:
    key_desc = spec.key_env or "an API key"
    return (
        f"{key_desc} is not set for the {spec.name} route. "
        f"Run `aiswmm login {spec.name}` to store a key, or export {key_desc}."
    )


def _auth_hint(spec: RouteSpec) -> str:
    key_desc = spec.key_env or "the route's API key"
    return (
        f" — authentication failed; your {key_desc} is missing, invalid, or "
        f"expired. Run `aiswmm login {spec.name}` to store a working key, then retry."
    )


__all__ = ["SUPPORTED_PROVIDERS", "make_provider"]
