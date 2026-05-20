"""Provider preflight (PRD-08 Phase A.3, audit #6; widened in PRD-09).

A first-run user typing ``aiswmm`` with no args lands in the
interactive shell that requires an LLM provider. The shell prints the
welcome banner, accepts a prompt, and then fails mid-turn with a
credential error. The user never sees the underlying cause — that the
provider was never configured.

This module is the boot-time diagnostic: before the interactive shell
hands control to the planner we check whether *some* provider is
configured. When nothing is configured we surface a guidance block on
stderr and the caller falls back to the rule planner so the user can
still discover the deterministic verbs even without a key.

The OpenAI check covers three locations:

* ``OPENAI_API_KEY`` environment variable
* ``~/.aiswmm/env`` (a shell-style env file the setup wizard writes)
* ``~/.aiswmm/config.toml`` (the persistent config the wizard maintains)

PRD-09 adds a fourth tier: the Claude Agent SDK provider, which walks
a Claude Pro/Max subscription through the locally installed ``claude``
CLI's OAuth credentials. We treat the presence of a parseable
credentials file (``~/.claude/.credentials.json``) as the OAuth
signal. Because routing a modeler onto their subscription quota is a
billing-relevant decision, the preflight only *reports*
``provider_name="claude_sdk"`` when the user has explicitly opted in
via ``provider.default = claude_sdk`` in ``~/.aiswmm/config.toml`` — a
bare OAuth file is surfaced as *available* but never silently selected.

A non-empty value in any of the OpenAI locations is enough to flip
``has_configured_provider`` to ``True``. We deliberately do *not* try
to validate the key (no network call) — the goal is to fail loud when
nothing is set, not to second-guess a key the user explicitly chose.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


_GUIDANCE_TEMPLATE = (
    "No LLM provider configured.\n"
    "\n"
    "Quick fix (option 1) — OpenAI API key:\n"
    "  export OPENAI_API_KEY=\"sk-...\"\n"
    "  or run `aiswmm setup --provider openai` to persist it.\n"
    "\n"
    "Quick fix (option 2) — Claude Pro/Max subscription:\n"
    "  run `claude login` to authenticate the Claude Code CLI, then\n"
    "  `aiswmm config set provider.default claude_sdk` to opt in.\n"
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


def _claude_credentials_paths() -> tuple[Path, ...]:
    """Return the candidate Claude Code OAuth credential file paths.

    The Claude Code CLI stores OAuth credentials in
    ``~/.claude/.credentials.json`` on Linux; ``~/.claude/auth.json``
    is accepted as a forward-compatible alias. On macOS the CLI keeps
    credentials in the Keychain — we do not parse the Keychain here;
    a present credentials file is one positive signal, and the SDK
    itself does the real auth at call time.
    """
    base = Path.home() / ".claude"
    return (base / ".credentials.json", base / "auth.json")


def detect_claude_oauth() -> bool:
    """Return True when a parseable Claude Code OAuth credentials file exists.

    A file that is missing, unreadable, empty, or not valid JSON is
    treated as *absent* rather than crashing the preflight — the goal
    is a best-effort availability signal, not strict validation. The
    SDK and the ``claude`` CLI perform the authoritative auth check at
    call time.
    """
    for path in _claude_credentials_paths():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.strip():
            continue
        try:
            json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        return True
    return False


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

    Resolution order:

    1. The user explicitly opted in to the Claude Agent SDK via
       ``provider.default = claude_sdk`` — this wins outright (the
       modeler asked for the subscription path), and OAuth presence
       only refines the guidance, never the selection.
    2. ``OPENAI_API_KEY`` env var → ``~/.aiswmm/env`` →
       ``~/.aiswmm/config.toml`` OpenAI key. Any non-empty value
       short-circuits to ``provider_name="openai"``.
    3. A bare Claude Code OAuth file with no explicit opt-in is
       surfaced as *available* (``has_configured_provider=True``,
       ``provider_name="claude_sdk"``) so the runtime does not drop to
       the rule planner — but only when no OpenAI key was found.

    A fully-negative result populates ``guidance_message`` with the
    two-option user-facing block.
    """
    explicit_default = _config_default_provider(_aiswmm_config_path())
    oauth_present = detect_claude_oauth()
    env_value = os.environ.get("OPENAI_API_KEY", "").strip()

    # Tier 1: explicit claude_sdk opt-in wins regardless of OpenAI keys.
    if explicit_default == "claude_sdk":
        return ProviderPreflightResult(
            has_configured_provider=True,
            provider_name="claude_sdk",
            fallback_planner="rule",
            guidance_message="",
        )

    # Tier 2: OpenAI key in env / env file / config.
    if env_value or _env_file_has_openai_key(_aiswmm_env_path()) or _config_file_has_openai_key(
        _aiswmm_config_path()
    ):
        return ProviderPreflightResult(
            has_configured_provider=True,
            provider_name="openai",
            fallback_planner="rule",
            guidance_message="",
        )

    # Tier 3: a Claude Code OAuth file is present even without an
    # explicit opt-in — surface claude_sdk as available so the runtime
    # does not needlessly drop to the rule planner.
    if oauth_present:
        return ProviderPreflightResult(
            has_configured_provider=True,
            provider_name="claude_sdk",
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
    "detect_claude_oauth",
]
