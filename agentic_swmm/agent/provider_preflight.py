"""Provider preflight — API-key selection for the interactive shell.

A first-run user typing ``aiswmm`` with no args lands in the
interactive shell that runs the LLM planner. The shell prints the
welcome banner, accepts a prompt, and then drives the planner. This
module is the boot-time diagnostic: before the shell hands control to
the planner we resolve *which* provider to use and whether its API key
is detectable.

Two API-key providers, both standard function-calling:

* ``openai`` (the shipped :data:`DEFAULT_PROVIDER`) — needs
  ``OPENAI_API_KEY``.
* ``anthropic`` (opt-in via ``provider.default = anthropic``) — needs
  ``ANTHROPIC_API_KEY``.

The resolved default is ``provider.default`` from
``~/.aiswmm/config.toml`` when set, else :data:`DEFAULT_PROVIDER`. A
provider is *configured* when its key is reachable from any of three
tiers: the environment, ``~/.aiswmm/env``, or the ``[<provider>]``
section of ``~/.aiswmm/config.toml``.

We deliberately do *not* validate the key (no network call) — the goal
is to pick the right backend and fail loud only when the selected
provider has no detectable key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agentic_swmm.config import DEFAULT_PROVIDER


# Env-var name carrying each provider's API key. Single source of truth
# so the detection tiers and the doctor / login surfaces agree.
_PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


_GUIDANCE_NO_CREDENTIALS = (
    "No LLM API key detected for the selected provider.\n"
    "\n"
    "OpenAI (default, billed per token):\n"
    "  run `aiswmm login --openai` to store a key, or\n"
    "  export OPENAI_API_KEY=\"sk-...\".\n"
    "\n"
    "Anthropic (opt-in, billed per token):\n"
    "  run `aiswmm login --anthropic` to store a key, or\n"
    "  export ANTHROPIC_API_KEY=\"sk-ant-...\".\n"
    "\n"
    "Continuing with the LLM planner; the first prompt will fail until a\n"
    "key for the selected provider is set."
)


_GUIDANCE_NOTHING_CONFIGURED = (
    "No LLM provider configured.\n"
    "\n"
    "OpenAI (default, billed per token):\n"
    "  run `aiswmm login --openai` to store a key, or\n"
    "  export OPENAI_API_KEY=\"sk-...\".\n"
    "\n"
    "Anthropic (opt-in, billed per token):\n"
    "  run `aiswmm login --anthropic` to store a key, or\n"
    "  export ANTHROPIC_API_KEY=\"sk-ant-...\".\n"
    "\n"
    "Continuing in rule-planner mode (no LLM, limited verbs available)."
)


@dataclass(frozen=True)
class ProviderPreflightResult:
    """Outcome of :func:`check_interactive_provider`.

    ``provider_name`` is the resolved provider (``"openai"`` or
    ``"anthropic"``) when one was selected; ``None`` only in the
    safety-net case where the resolved default is an unknown provider.
    ``fallback_planner`` is what the CLI should dispatch when the
    user-supplied planner is unavailable (always ``"rule"`` today).
    ``guidance_message`` is the multi-line block the caller writes to
    stderr: empty when the selected provider's key is present, a soft
    warning when the provider is selected without a detectable key, and
    the full no-provider block in the rule-fallback safety net.
    """

    has_configured_provider: bool
    provider_name: str | None
    fallback_planner: str
    guidance_message: str


def _aiswmm_env_path() -> Path:
    """Return ``~/.aiswmm/env`` resolved from ``$HOME``."""
    return Path.home() / ".aiswmm" / "env"


def _aiswmm_config_path() -> Path:
    """Return ``~/.aiswmm/config.toml`` resolved from ``$HOME``."""
    return Path.home() / ".aiswmm" / "config.toml"


def _config_default_provider(path: Path) -> str | None:
    """Return the ``provider.default`` value from the config TOML.

    Reads the literal ``default = "..."`` line inside the
    ``[provider]`` section. Returns ``None`` when the file is missing,
    unreadable, or does not declare the key — we keep the shallow
    line-scanner used elsewhere in this module rather than importing a
    TOML parser.
    """
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    in_provider_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_provider_section = line[1:-1].strip().lower() == "provider"
            continue
        if not in_provider_section or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip().lower() != "default":
            continue
        return value.strip().strip("'\"") or None
    return None


def _env_file_key_value(path: Path, var_name: str) -> str | None:
    """Return the non-empty ``var_name`` declared in the env file.

    Tolerant of ``export FOO=bar`` and ``FOO="bar"`` shapes; a malformed
    line is ignored rather than crashing the preflight. Returns ``None``
    when the file is missing, unreadable, or declares no usable value.
    """
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != var_name:
            continue
        value = value.strip().strip("'\"")
        if value:
            return value
    return None


def _config_file_key_value(path: Path, section: str) -> str | None:
    """Return the API key the config TOML declares for ``section``.

    We do not import a TOML parser to keep dependencies flat; the
    config is shallow and the wizard writes a stable shape. A literal
    ``api_key = "..."`` (or ``<section>_api_key = "..."``) line under
    the ``[<section>]`` section counts as configured.
    """
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    section_key = f"{section}_api_key"
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = line[1:-1].strip().lower() == section
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip().strip("'\"")
        if not value:
            continue
        if key == section_key or (in_section and key == "api_key"):
            return value
    return None


def provider_key_value(provider_name: str) -> str | None:
    """Return ``provider_name``'s API key, or ``None`` when unreachable.

    This is the resolver the runtime and the diagnostics share. Before it
    existed, `aiswmm login` wrote ``~/.aiswmm/env`` and the providers read
    only ``os.environ``, so a fully onboarded user was told the key was
    present by ``doctor`` and missing by the runtime.

    Precedence, highest first:

    1. the provider's environment variable, so a shell ``export``
       overrides stored state for one session without editing files;
    2. ``~/.aiswmm/env``, where ``aiswmm login`` writes;
    3. the ``[<provider>]`` section of ``~/.aiswmm/config.toml``.

    Unknown providers (no key mapping) return ``None``.
    """
    var_name = _PROVIDER_KEY_ENV.get(provider_name)
    if not var_name:
        return None
    from_env = os.environ.get(var_name, "").strip()
    if from_env:
        return from_env
    from_env_file = _env_file_key_value(_aiswmm_env_path(), var_name)
    if from_env_file:
        return from_env_file
    return _config_file_key_value(_aiswmm_config_path(), provider_name)


def provider_key_present(provider_name: str) -> bool:
    """Return True when ``provider_name``'s API key is reachable.

    Deliberately derived from :func:`provider_key_value` rather than
    re-deriving presence on its own. What ``doctor`` reports is then, by
    construction, an answer about the very value the runtime will use:
    the two cannot drift into disagreeing again.
    """
    return provider_key_value(provider_name) is not None


def _openai_key_present() -> bool:
    """Back-compat shim: True when an OpenAI key is reachable.

    Retained because the login ``--status`` surface imports this name.
    """
    return provider_key_present("openai")


def check_interactive_provider() -> ProviderPreflightResult:
    """Resolve the interactive planner provider from the two API keys.

    Resolution order:

    1. The resolved default is the configured ``provider.default`` when
       set, else :data:`DEFAULT_PROVIDER` (``openai``).
    2. When the resolved default is a known provider (``openai`` /
       ``anthropic``) it is selected. We keep the LLM planner even if no
       key is detected — the provider authenticates at call time and a
       user who just exported a key in another shell must not be dropped
       to the rule planner. When the selected provider's key is not
       detectable we attach a soft warning (``aiswmm login`` hint).
    3. Safety net: if the resolved default is some unknown provider, we
       fall back to the rule planner with the full no-provider guidance.
    """
    explicit_default = _config_default_provider(_aiswmm_config_path())
    resolved_default = explicit_default or DEFAULT_PROVIDER

    if resolved_default in _PROVIDER_KEY_ENV:
        key_present = provider_key_present(resolved_default)
        guidance = "" if key_present else _GUIDANCE_NO_CREDENTIALS
        return ProviderPreflightResult(
            has_configured_provider=True,
            provider_name=resolved_default,
            fallback_planner="rule",
            guidance_message=guidance,
        )

    # Safety net: an unknown configured default we cannot honour. Drop to
    # the rule planner and surface the full guidance block.
    return ProviderPreflightResult(
        has_configured_provider=False,
        provider_name=None,
        fallback_planner="rule",
        guidance_message=_GUIDANCE_NOTHING_CONFIGURED,
    )


__all__ = [
    "ProviderPreflightResult",
    "check_interactive_provider",
    "provider_key_present",
]
