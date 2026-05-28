"""``aiswmm login`` — an independent, extensible LLM-auth subsystem.

Login is deliberately *not* welded to any one provider. The command is
a thin dispatcher over a ``provider -> handler`` registry
(:data:`_LOGIN_HANDLERS`), so adding a future auth method (``--anthropic``,
``--gemini``, …) is a single new handler plus one registry entry — the
same factory-only spirit as :mod:`agentic_swmm.providers.factory`.

Surfaces:

* ``aiswmm login`` (default) — the Claude Pro/Max **subscription** path.
  Verifies the ``claude`` CLI is present, logs in via ``claude login``
  when needed (reusing the Keychain/JSON detection so a logged-in macOS
  user is recognised), persists ``provider.default = claude_sdk``, and
  checks that ``claude_agent_sdk`` is importable.
* ``aiswmm login --openai`` — store an OpenAI API key in
  ``~/.aiswmm/env`` (file mode 0600), set ``provider.default = openai``
  and ``openai.model = gpt-5.5``. The key is never echoed.
* ``aiswmm login --status`` — print the current auth state (default
  provider, subscription detected, OpenAI key present). No secrets.

The handlers never print or log credential material.
"""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.config import (
    DEFAULT_OPENAI_MODEL,
    config_dir,
    load_config,
    set_config_value,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "login",
        help="Authenticate an LLM provider (Claude subscription by default).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--openai",
        action="store_true",
        help="Opt in to OpenAI: store an API key and set it as the default provider.",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Print the current auth state (no secrets) and exit.",
    )
    register_example_flag(parser, example_text="aiswmm login")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    if getattr(args, "status", False):
        return _login_status(args)
    # Resolve which login handler to dispatch. A bare ``aiswmm login``
    # takes the subscription (claude_sdk) path; ``--openai`` selects the
    # OpenAI handler. Future flags add entries to ``_LOGIN_HANDLERS``.
    provider = "openai" if getattr(args, "openai", False) else "claude_sdk"
    handler = _LOGIN_HANDLERS.get(provider)
    if handler is None:  # pragma: no cover - defensive; choices are constrained
        print(f"error: no login handler for provider {provider!r}", file=sys.stderr)
        return 1
    return handler(args)


# ---------------------------------------------------------------------------
# Subscription (claude_sdk) handler
# ---------------------------------------------------------------------------


def _login_claude_sdk(args: argparse.Namespace) -> int:
    """Authenticate the Claude Pro/Max subscription path.

    Steps: verify the ``claude`` CLI is on PATH; if the user is not
    already logged in (Keychain/JSON detection), shell out to
    ``claude login`` interactively; persist ``provider.default =
    claude_sdk``; warn (non-fatal) if ``claude_agent_sdk`` is not
    importable so the user knows to install the extra.
    """
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        print(
            "The `claude` CLI was not found on PATH.\n"
            "Install Claude Code (https://docs.claude.com/claude-code) and\n"
            "re-run `aiswmm login`.",
            file=sys.stderr,
        )
        return 1

    if _subscription_detected():
        print("Already logged in to the Claude Code subscription.")
    else:
        print("Launching `claude login` to authenticate your subscription...")
        try:
            completed = subprocess.run([claude_bin, "login"], check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"error: failed to launch `claude login`: {exc}", file=sys.stderr)
            return 1
        if completed.returncode != 0:
            print(
                "`claude login` did not complete successfully. "
                "Re-run `aiswmm login` after logging in.",
                file=sys.stderr,
            )
            return 1

    set_config_value("provider.default", "claude_sdk")
    print("Default provider set to claude_sdk (subscription path).")

    if not _claude_agent_sdk_importable():
        print(
            "Note: the `claude_agent_sdk` package is not importable. Install\n"
            'it with: python3.11 -m pip install -e ".[claude]"',
            file=sys.stderr,
        )
    return 0


# ---------------------------------------------------------------------------
# OpenAI handler
# ---------------------------------------------------------------------------


def _login_openai(args: argparse.Namespace) -> int:
    """Store an OpenAI API key and select OpenAI as the default provider.

    The key is read without echo (``getpass``) unless supplied via the
    ``AISWMM_LOGIN_OPENAI_KEY`` env var (used by tests / non-interactive
    automation). It is written to ``~/.aiswmm/env`` with file mode 0600
    and never printed back.
    """
    key = os.environ.get("AISWMM_LOGIN_OPENAI_KEY")
    if not key:
        try:
            key = getpass.getpass("OpenAI API key (input hidden): ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted; no key stored.", file=sys.stderr)
            return 1
    key = (key or "").strip()
    if not key:
        print("error: empty API key; nothing stored.", file=sys.stderr)
        return 1

    env_path = _write_openai_key_to_env(key)
    set_config_value("provider.default", "openai")
    set_config_value("openai.model", DEFAULT_OPENAI_MODEL)
    print(f"Stored OpenAI API key in {env_path} (mode 0600).")
    print(f"Default provider set to openai; openai.model set to {DEFAULT_OPENAI_MODEL}.")
    print(
        "Note: source the env file (or restart your shell) so OPENAI_API_KEY "
        "is exported."
    )
    return 0


def _write_openai_key_to_env(key: str) -> Path:
    """Write/replace the ``OPENAI_API_KEY`` line in ``~/.aiswmm/env`` (0600).

    Preserves any other lines already in the file; only the
    ``OPENAI_API_KEY`` export is rewritten. The file is created with
    restrictive permissions and re-chmod'd on every write so an existing
    loose-mode file is tightened.
    """
    env_path = config_dir() / "env"
    env_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if env_path.is_file():
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                stripped = raw.strip()
                body = stripped[len("export ") :] if stripped.startswith("export ") else stripped
                name = body.split("=", 1)[0].strip() if "=" in body else ""
                if name == "OPENAI_API_KEY":
                    continue  # drop the old key line; we rewrite it below
                lines.append(raw)
        except OSError:
            lines = []

    lines.append(f'export OPENAI_API_KEY="{key}"')
    # Create with 0600 from the start where possible, then write.
    fd = os.open(str(env_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
    os.chmod(env_path, 0o600)
    return env_path


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def _login_status(args: argparse.Namespace) -> int:
    """Print the current auth state — default provider + signals, no secrets."""
    default_provider = str(load_config().get("provider.default", "claude_sdk"))
    subscription = _subscription_detected()
    openai_key = _openai_key_present()
    claude_cli = shutil.which("claude") is not None
    sdk_ok = _claude_agent_sdk_importable()

    print(f"default provider:        {default_provider}")
    print(f"claude subscription:     {'detected' if subscription else 'not detected'}")
    print(f"claude CLI on PATH:      {'yes' if claude_cli else 'no'}")
    print(f"claude_agent_sdk:        {'importable' if sdk_ok else 'not installed'}")
    print(f"OpenAI API key present:  {'yes' if openai_key else 'no'}")
    return 0


# ---------------------------------------------------------------------------
# Shared probes (no secret material crosses these)
# ---------------------------------------------------------------------------


def _subscription_detected() -> bool:
    """Reuse the preflight's Keychain/JSON/ANTHROPIC_API_KEY detection."""
    from agentic_swmm.agent.provider_preflight import detect_claude_oauth

    return detect_claude_oauth()


def _openai_key_present() -> bool:
    """Reuse the preflight's env / env-file / config OpenAI-key detection."""
    from agentic_swmm.agent.provider_preflight import _openai_key_present as _probe

    return _probe()


def _claude_agent_sdk_importable() -> bool:
    """Return True iff ``claude_agent_sdk`` can be imported."""
    import importlib.util

    return importlib.util.find_spec("claude_agent_sdk") is not None


# Registry: provider name -> login handler. Adding a backend's login
# method is a one-line entry here plus the handler above.
_LOGIN_HANDLERS = {
    "claude_sdk": _login_claude_sdk,
    "openai": _login_openai,
}


__all__ = ["register", "main"]
