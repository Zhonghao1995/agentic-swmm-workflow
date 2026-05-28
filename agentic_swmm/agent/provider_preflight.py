"""Provider preflight — subscription-first selection for the interactive shell.

A first-run user typing ``aiswmm`` with no args lands in the
interactive shell that runs the LLM planner. The shell prints the
welcome banner, accepts a prompt, and then drives the planner. This
module is the boot-time diagnostic: before the shell hands control to
the planner we resolve *which* provider to use and whether any
credentials are detectable.

Subscription-first (the shipped default is ``claude_sdk``):

* The resolved default provider is ``provider.default`` from
  ``~/.aiswmm/config.toml`` when set, else :data:`DEFAULT_PROVIDER`
  (``claude_sdk``).
* The subscription signal is a logged-in Claude Code session. On Linux
  the CLI stores OAuth credentials in ``~/.claude/.credentials.json``
  (``auth.json`` accepted as an alias); **on macOS the CLI keeps them
  in the login Keychain**, so we additionally probe
  ``security find-generic-password -s "Claude Code-credentials"`` and
  treat exit 0 as a positive signal. We check the *returncode only* —
  never pass ``-w``, never read or log the secret. A raw
  ``ANTHROPIC_API_KEY`` is also treated as a credential signal.
* OpenAI is opt-in: an explicit ``provider.default = openai`` selects
  it, and an ``OPENAI_API_KEY`` (env / env-file / config) selects it
  *only when no subscription is detected* — a logged-in subscription
  user is never silently routed to paid OpenAI.
* When the resolved default is ``claude_sdk`` we select it even if no
  credentials are detected (the SDK authenticates at call time via the
  ``claude`` CLI — a logged-in macOS user must NOT be dropped to the
  rule planner). When no credentials are visible we attach a soft
  warning pointing at ``aiswmm login`` but still select claude_sdk.

We deliberately do *not* validate any key (no network call) — the goal
is to pick the right backend and fail loud only when nothing is usable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agentic_swmm.config import DEFAULT_PROVIDER


# Subscription-first guidance: lead with the zero-marginal-cost
# subscription path (``aiswmm login`` / ``claude login``); the OpenAI
# key is the secondary, opt-in option.
_GUIDANCE_NO_CREDENTIALS = (
    "No LLM credentials detected.\n"
    "\n"
    "Recommended — Claude Pro/Max subscription (zero per-token cost):\n"
    "  run `aiswmm login` (or `claude login`) to authenticate the\n"
    "  Claude Code CLI. claude_sdk is already the default provider.\n"
    "\n"
    "Alternative — OpenAI API key (opt-in, billed per token):\n"
    "  run `aiswmm login --openai` to store a key, or\n"
    "  export OPENAI_API_KEY=\"sk-...\".\n"
    "\n"
    "Continuing with the claude_sdk subscription path; the first prompt\n"
    "will fail if you are not logged in."
)


_GUIDANCE_NOTHING_CONFIGURED = (
    "No LLM provider configured.\n"
    "\n"
    "Recommended — Claude Pro/Max subscription (zero per-token cost):\n"
    "  run `aiswmm login` (or `claude login`) to authenticate the\n"
    "  Claude Code CLI, then describe what you want.\n"
    "\n"
    "Alternative — OpenAI API key (opt-in, billed per token):\n"
    "  run `aiswmm login --openai` to store a key, or\n"
    "  export OPENAI_API_KEY=\"sk-...\".\n"
    "\n"
    "Continuing in rule-planner mode (no LLM, limited verbs available)."
)


# Keychain service name the Claude Code CLI uses on macOS.
_MACOS_KEYCHAIN_SERVICE = "Claude Code-credentials"


@dataclass(frozen=True)
class ProviderPreflightResult:
    """Outcome of :func:`check_interactive_provider`.

    ``provider_name`` is the resolved provider (``"claude_sdk"`` or
    ``"openai"``) when one was selected; ``None`` only in the safety-net
    case where the resolved default is an unknown provider.
    ``fallback_planner`` is what the CLI should dispatch when the
    user-supplied planner is unavailable (always ``"rule"`` today).
    ``guidance_message`` is the multi-line block the caller writes to
    stderr: empty when credentials are present, a soft warning when the
    subscription default is selected without detectable credentials, and
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


def _claude_credentials_paths() -> tuple[Path, ...]:
    """Return the candidate Claude Code OAuth credential file paths.

    The Claude Code CLI stores OAuth credentials in
    ``~/.claude/.credentials.json`` on Linux; ``~/.claude/auth.json``
    is accepted as a forward-compatible alias. On macOS the CLI keeps
    credentials in the login Keychain — see
    :func:`_detect_macos_keychain_credentials`.
    """
    base = Path.home() / ".claude"
    return (base / ".credentials.json", base / "auth.json")


def _detect_macos_keychain_credentials() -> bool:
    """Return True when a Claude Code credential exists in the macOS Keychain.

    The Claude Code CLI on macOS stores its OAuth credentials as a
    generic-password Keychain item named ``Claude Code-credentials``
    instead of writing a JSON file. ``detect_claude_oauth`` therefore
    returns False for a logged-in macOS user unless we probe the
    Keychain here.

    Security contract: we run ``security find-generic-password -s
    "Claude Code-credentials"`` and inspect the **returncode only**. We
    never pass ``-w`` (which would print the secret to stdout) and we
    never read, return, or log the credential material. A non-zero exit
    (item absent) or any subprocess failure (``security`` missing,
    timeout) is treated as "absent" rather than crashing the preflight.
    """
    if sys.platform != "darwin":
        return False
    try:
        completed = subprocess.run(
            ["security", "find-generic-password", "-s", _MACOS_KEYCHAIN_SERVICE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def detect_claude_oauth() -> bool:
    """Return True when a Claude Code subscription credential is detectable.

    Three positive signals, any of which counts:

    1. A parseable OAuth credentials file under ``~/.claude`` (Linux).
    2. A ``Claude Code-credentials`` Keychain item on macOS (the
       returncode-only probe in
       :func:`_detect_macos_keychain_credentials`).
    3. A non-empty ``ANTHROPIC_API_KEY`` env var (raw-API fallback the
       SDK can use).

    A file that is missing, unreadable, empty, or not valid JSON is
    treated as *absent* rather than crashing the preflight — the goal is
    a best-effort availability signal, not strict validation. The SDK
    and the ``claude`` CLI perform the authoritative auth check at call
    time.
    """
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return True
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
    return _detect_macos_keychain_credentials()


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


def _openai_key_present() -> bool:
    """Return True when an OpenAI key is reachable from any of the tiers.

    Checks the ``OPENAI_API_KEY`` env var, then ``~/.aiswmm/env``, then
    the ``[openai]`` section of ``~/.aiswmm/config.toml``.
    """
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return True
    if _env_file_has_openai_key(_aiswmm_env_path()):
        return True
    return _config_file_has_openai_key(_aiswmm_config_path())


def check_interactive_provider() -> ProviderPreflightResult:
    """Resolve the interactive planner provider, subscription-first.

    Resolution order:

    1. The resolved default is the configured ``provider.default`` when
       set, else :data:`DEFAULT_PROVIDER` (``claude_sdk``).
    2. OpenAI is selected when the user explicitly set
       ``provider.default = openai``, OR when an OpenAI key is present
       *and* no Claude subscription credential is detected (a logged-in
       subscription user is never silently routed to paid OpenAI).
    3. Otherwise the subscription default ``claude_sdk`` is selected. It
       is selected even with no detectable credentials — the SDK
       authenticates at call time, and a logged-in macOS user must not
       be dropped to the rule planner. When no credential is detected we
       attach a soft warning (``aiswmm login`` hint) but still select
       claude_sdk.
    4. Safety net: if the resolved default is some unknown provider, we
       fall back to the rule planner with the full no-provider guidance.
    """
    explicit_default = _config_default_provider(_aiswmm_config_path())
    resolved_default = explicit_default or DEFAULT_PROVIDER
    subscription_detected = detect_claude_oauth()
    openai_key_present = _openai_key_present()

    # OpenAI is opt-in: explicit selection, or a key present with no
    # competing subscription. A logged-in subscription user keeps the
    # subscription even if a stale OpenAI key lingers.
    if explicit_default == "openai" or (
        openai_key_present and not subscription_detected
    ):
        return ProviderPreflightResult(
            has_configured_provider=True,
            provider_name="openai",
            fallback_planner="rule",
            guidance_message="",
        )

    # Subscription default (the shipped default, or an explicit opt-in).
    if resolved_default == "claude_sdk":
        guidance = "" if subscription_detected else _GUIDANCE_NO_CREDENTIALS
        return ProviderPreflightResult(
            has_configured_provider=True,
            provider_name="claude_sdk",
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
    "detect_claude_oauth",
]
