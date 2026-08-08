"""Route table: every LLM connection recipe aiswmm knows how to drive.

A *route* is a named recipe {wire format, default endpoint, key env var,
default model, curated model menu}. This table is the single source of
truth (ADR-0008): the factory, the login command, the setup wizard, the
preflight resolver, doctor, and every ``--provider`` argparse site all
derive from it. Adding a provider that speaks one of the three wire
formats is one :class:`RouteSpec` entry here, nothing else.

Three wire formats cover every listed route:

* ``openai-responses`` — OpenAI Responses API. Used by ``openai`` and by
  ``codex`` (a local OpenAI-compatible gateway that fronts a ChatGPT
  subscription; aiswmm treats it as a plain endpoint).
* ``openai-chat`` — OpenAI chat/completions with function calling. The
  lingua franca of third-party and local endpoints (OpenRouter,
  DeepSeek, Groq, Gemini's compatibility endpoint, Ollama, LM Studio,
  vLLM, and any ``custom`` endpoint).
* ``anthropic-messages`` — native Anthropic Messages API.

This module is import-light on purpose (stdlib ``os``/``dataclasses``
only, no intra-package imports) so config, preflight, factory, and the
CLI can all import it without cycles: routes <- config <- preflight <-
factory <- commands.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable


WIRE_OPENAI_RESPONSES = "openai-responses"
WIRE_OPENAI_CHAT = "openai-chat"
WIRE_ANTHROPIC_MESSAGES = "anthropic-messages"

WIRE_FORMATS = (
    WIRE_OPENAI_RESPONSES,
    WIRE_OPENAI_CHAT,
    WIRE_ANTHROPIC_MESSAGES,
)


@dataclass(frozen=True)
class RouteSpec:
    """One LLM connection recipe.

    ``keyless=True`` means the route is usable without a stored key (a
    key, when present via ``key_env`` or config, is still sent). Routes
    with ``keyless=False`` are not *ready* until a key is resolvable.

    ``base_url`` is the API root (``.../v1``); the wire client derives
    its endpoint path (``/responses``, ``/chat/completions``,
    ``/messages``) from it. Empty ``base_url`` (only ``custom``) means
    the user must supply one via config or env before the route works.

    ``detect_url`` is an optional local liveness/models probe the setup
    wizard uses for detection hints and dynamic model listings; it is
    never contacted by the runtime.
    """

    name: str
    label: str
    wire: str
    base_url: str
    key_env: str
    default_model: str
    model_menu: tuple[str, ...]
    keyless: bool = False
    detect_url: str = ""
    hint: str = ""


ROUTES: dict[str, RouteSpec] = {
    spec.name: spec
    for spec in (
        RouteSpec(
            name="openai",
            label="OpenAI",
            wire=WIRE_OPENAI_RESPONSES,
            base_url="https://api.openai.com/v1",
            key_env="OPENAI_API_KEY",
            default_model="gpt-5.5",
            model_menu=("gpt-5.5", "gpt-5.5-mini", "gpt-5.4"),
            hint="Billed per token; the shipped default.",
        ),
        RouteSpec(
            name="anthropic",
            label="Anthropic",
            wire=WIRE_ANTHROPIC_MESSAGES,
            base_url="https://api.anthropic.com/v1",
            key_env="ANTHROPIC_API_KEY",
            default_model="claude-sonnet-4-6",
            model_menu=("claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5"),
            hint="Billed per token.",
        ),
        RouteSpec(
            name="codex",
            # chat/completions on purpose: it is the surface both major
            # gateways (CLIProxyAPI :8317, OmniRoute :20128) implement;
            # CLIProxyAPI's /v1/responses conversion has open bugs and
            # OmniRoute does not expose /v1/responses at all.
            label="Local gateway (ChatGPT subscription, Codex-compatible gateway)",
            wire=WIRE_OPENAI_CHAT,
            base_url="http://localhost:8317/v1",
            key_env="AISWMM_CODEX_API_KEY",
            default_model="gpt-5.6-sol",
            model_menu=("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"),
            keyless=True,
            detect_url="http://localhost:8317/v1/models",
            hint="Needs a local OpenAI-compatible gateway (CLIProxyAPI or OmniRoute) running.",
        ),
        RouteSpec(
            name="openrouter",
            label="OpenRouter",
            wire=WIRE_OPENAI_CHAT,
            base_url="https://openrouter.ai/api/v1",
            key_env="OPENROUTER_API_KEY",
            default_model="openai/gpt-5.5",
            model_menu=(
                "openai/gpt-5.5",
                "anthropic/claude-sonnet-4-6",
                "deepseek/deepseek-chat",
                "qwen/qwen3-coder",
            ),
            hint="One key, hundreds of models.",
        ),
        RouteSpec(
            name="deepseek",
            label="DeepSeek",
            wire=WIRE_OPENAI_CHAT,
            base_url="https://api.deepseek.com/v1",
            key_env="DEEPSEEK_API_KEY",
            default_model="deepseek-chat",
            model_menu=("deepseek-chat", "deepseek-reasoner"),
        ),
        RouteSpec(
            name="groq",
            label="Groq",
            wire=WIRE_OPENAI_CHAT,
            base_url="https://api.groq.com/openai/v1",
            key_env="GROQ_API_KEY",
            default_model="llama-3.3-70b-versatile",
            model_menu=("llama-3.3-70b-versatile", "qwen/qwen3-32b"),
        ),
        RouteSpec(
            name="gemini",
            label="Google Gemini (OpenAI-compatible endpoint)",
            wire=WIRE_OPENAI_CHAT,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            key_env="GEMINI_API_KEY",
            default_model="gemini-2.5-pro",
            model_menu=("gemini-2.5-pro", "gemini-2.5-flash"),
        ),
        RouteSpec(
            name="ollama",
            label="Ollama (local models, keyless)",
            wire=WIRE_OPENAI_CHAT,
            base_url="http://localhost:11434/v1",
            key_env="",
            default_model="llama3.1:8b",
            model_menu=("llama3.1:8b", "qwen3:8b"),
            keyless=True,
            detect_url="http://localhost:11434/api/tags",
            hint="Free local fallback; needs the Ollama app running.",
        ),
        RouteSpec(
            name="lmstudio",
            label="LM Studio (local models, keyless)",
            wire=WIRE_OPENAI_CHAT,
            base_url="http://localhost:1234/v1",
            key_env="",
            default_model="",
            model_menu=(),
            keyless=True,
            detect_url="http://localhost:1234/v1/models",
            hint="Pick the loaded model from the live listing.",
        ),
        RouteSpec(
            name="custom",
            label="Custom OpenAI-compatible endpoint",
            wire=WIRE_OPENAI_CHAT,
            base_url="",
            key_env="AISWMM_CUSTOM_API_KEY",
            default_model="",
            model_menu=(),
            keyless=True,
            hint="Any /v1 endpoint (vLLM, a gateway, a corporate proxy).",
        ),
    )
}


def route_names() -> tuple[str, ...]:
    """Canonical route-name tuple, table order. Argparse choices derive here."""
    return tuple(ROUTES)


def get_route(name: str) -> RouteSpec:
    """Return the :class:`RouteSpec` for ``name`` or raise ``ValueError``."""
    try:
        return ROUTES[name]
    except KeyError:
        known = ", ".join(route_names())
        raise ValueError(f"unknown provider route {name!r}; known routes: {known}") from None


def _env_token(name: str) -> str:
    return name.upper().replace("-", "_")


def model_env_var(name: str) -> str:
    """Env var overriding ``name``'s model (``AISWMM_<ROUTE>_MODEL``)."""
    return f"AISWMM_{_env_token(name)}_MODEL"


def base_url_env_var(name: str) -> str:
    """Env var overriding ``name``'s base URL (``AISWMM_<ROUTE>_BASE_URL``)."""
    return f"AISWMM_{_env_token(name)}_BASE_URL"


def resolved_base_url(name: str, config_get: Callable[[str, Any], Any] | None = None) -> str:
    """Return the effective API root for route ``name``.

    Precedence: ``AISWMM_<ROUTE>_BASE_URL`` env > ``[<route>] base_url``
    config > the table default. Trailing slashes are stripped so wire
    clients can append their endpoint path unconditionally.
    """
    spec = get_route(name)
    from_env = os.environ.get(base_url_env_var(name), "").strip()
    if from_env:
        return from_env.rstrip("/")
    if config_get is not None:
        from_config = str(config_get(f"{name}.base_url", "") or "").strip()
        if from_config:
            return from_config.rstrip("/")
    return spec.base_url.rstrip("/")


def resolved_model(name: str, config_get: Callable[[str, Any], Any] | None = None) -> str:
    """Return the effective model id for route ``name``.

    Precedence: ``AISWMM_<ROUTE>_MODEL`` env > ``[<route>] model`` config
    > the table default (may be empty for ``custom``/``lmstudio``, where
    the wizard or an explicit ``--model`` supplies one).
    """
    spec = get_route(name)
    from_env = os.environ.get(model_env_var(name), "").strip()
    if from_env:
        return from_env
    if config_get is not None:
        from_config = str(config_get(f"{name}.model", "") or "").strip()
        if from_config:
            return from_config
    return spec.default_model


__all__ = [
    "WIRE_OPENAI_RESPONSES",
    "WIRE_OPENAI_CHAT",
    "WIRE_ANTHROPIC_MESSAGES",
    "WIRE_FORMATS",
    "RouteSpec",
    "ROUTES",
    "route_names",
    "get_route",
    "model_env_var",
    "base_url_env_var",
    "resolved_base_url",
    "resolved_model",
]
