"""Provider preflight (PRD-08 Phase A.3, audit #6).

A first-run user typing ``aiswmm`` with no args lands in the
interactive shell that requires ``OPENAI_API_KEY``. The shell prints
the welcome banner, accepts a prompt, and then fails mid-turn with a
401-style error. The user never sees the underlying cause — that the
provider was never configured.

This module is the boot-time diagnostic: before the interactive shell
hands control to the planner we check whether *some* provider is
configured. When nothing is configured we surface a guidance block on
stderr and the caller falls back to the rule planner so the user can
still discover the deterministic verbs even without a key.

The check covers three locations:

* ``OPENAI_API_KEY`` environment variable
* ``~/.aiswmm/env`` (a shell-style env file the setup wizard writes)
* ``~/.aiswmm/config.toml`` (the persistent config the wizard maintains)

A non-empty value in any of them is enough to flip
``has_configured_provider`` to ``True``. We deliberately do *not* try
to validate the key (no network call) — the goal is to fail loud when
nothing is set, not to second-guess a key the user explicitly chose.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_GUIDANCE_TEMPLATE = (
    "OpenAI API key not configured.\n"
    "\n"
    "Quick fix:\n"
    "  export OPENAI_API_KEY=\"sk-...\"\n"
    "  or run `aiswmm setup --provider openai` to persist it.\n"
    "\n"
    "Continuing in rule-planner mode (no LLM, limited verbs available)."
)


@dataclass(frozen=True)
class ProviderPreflightResult:
    """Outcome of :func:`check_interactive_provider`.

    ``provider_name`` is the configured provider when one was found
    (today only ``"openai"``); ``None`` when nothing is configured.
    ``fallback_planner`` is what the CLI should dispatch when the
    user-supplied planner is unavailable (always ``"rule"`` today).
    ``guidance_message`` is the multi-line block the caller writes to
    stderr; it stays non-empty when ``has_configured_provider`` is
    ``False`` and empty otherwise.
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


def _env_file_has_openai_key(path: Path) -> bool:
    """Return True when the env file declares a non-empty OPENAI_API_KEY.

    Tolerant of ``export FOO=bar`` and ``FOO="bar"`` shapes; a malformed
    line is ignored rather than crashing the preflight.
    """
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "OPENAI_API_KEY":
            continue
        value = value.strip().strip("'\"")
        if value:
            return True
    return False


def _config_file_has_openai_key(path: Path) -> bool:
    """Return True when the config TOML declares an openai API key entry.

    We do not import a TOML parser to keep dependencies flat; the
    config is shallow and the wizard writes a stable shape. A literal
    ``openai_api_key = "..."`` or ``api_key = "..."`` line under an
    ``[openai]`` section counts as configured.
    """
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    in_openai_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_openai_section = line[1:-1].strip().lower() == "openai"
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip().strip("'\"")
        if not value:
            continue
        if key in {"openai_api_key", "api_key"} and (
            in_openai_section or key == "openai_api_key"
        ):
            return True
    return False


def check_interactive_provider() -> ProviderPreflightResult:
    """Return whether an interactive planner provider is configured.

    Order of precedence: env var → ``~/.aiswmm/env`` → ``~/.aiswmm/config.toml``.
    Any non-empty value short-circuits to ``has_configured_provider=True``.
    A negative result populates ``guidance_message`` with the
    user-facing block.
    """
    env_value = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_value:
        return ProviderPreflightResult(
            has_configured_provider=True,
            provider_name="openai",
            fallback_planner="rule",
            guidance_message="",
        )

    if _env_file_has_openai_key(_aiswmm_env_path()):
        return ProviderPreflightResult(
            has_configured_provider=True,
            provider_name="openai",
            fallback_planner="rule",
            guidance_message="",
        )

    if _config_file_has_openai_key(_aiswmm_config_path()):
        return ProviderPreflightResult(
            has_configured_provider=True,
            provider_name="openai",
            fallback_planner="rule",
            guidance_message="",
        )

    return ProviderPreflightResult(
        has_configured_provider=False,
        provider_name=None,
        fallback_planner="rule",
        guidance_message=_GUIDANCE_TEMPLATE,
    )


__all__ = [
    "ProviderPreflightResult",
    "check_interactive_provider",
]
