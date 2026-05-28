"""``aiswmm login`` — manage the two providers' API keys.

Login is a thin dispatcher over a ``provider -> handler`` registry
(:data:`_LOGIN_HANDLERS`), so adding a future provider's key is a
single new handler plus one registry entry — the same factory-only
spirit as :mod:`agentic_swmm.providers.factory`.

Surfaces:

* ``aiswmm login`` (default) — store the key for the *current default*
  provider (``provider.default``, normally ``openai``).
* ``aiswmm login --openai`` — store ``OPENAI_API_KEY`` and set
  ``provider.default = openai`` (+ ``openai.model`` default).
* ``aiswmm login --anthropic`` — store ``ANTHROPIC_API_KEY`` and set
  ``provider.default = anthropic`` (+ ``anthropic.model`` default).
* ``aiswmm login --status`` — print the default provider and which
  keys are present. No secrets.

Keys are written to ``~/.aiswmm/env`` (file mode 0600) and never
echoed. The handlers never print or log credential material.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.config import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_PROVIDER,
    config_dir,
    load_config,
    set_config_value,
)


# Per-provider key/model/login-env facts. Single source of truth so the
# handlers, the status surface, and the bare-login prompt all agree.
_PROVIDER_SPECS = {
    "openai": {
        "env_var": "OPENAI_API_KEY",
        "model_key": "openai.model",
        "model_default": DEFAULT_OPENAI_MODEL,
        "login_env": "AISWMM_LOGIN_OPENAI_KEY",
        "label": "OpenAI",
    },
    "anthropic": {
        "env_var": "ANTHROPIC_API_KEY",
        "model_key": "anthropic.model",
        "model_default": DEFAULT_ANTHROPIC_MODEL,
        "login_env": "AISWMM_LOGIN_ANTHROPIC_KEY",
        "label": "Anthropic",
    },
}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "login",
        help="Store an LLM provider API key (OpenAI by default).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--openai",
        action="store_true",
        help="Store an OpenAI API key and set OpenAI as the default provider.",
    )
    group.add_argument(
        "--anthropic",
        action="store_true",
        help="Store an Anthropic API key and set Anthropic as the default provider.",
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
    # Resolve which provider's key to store. ``--openai`` / ``--anthropic``
    # are explicit; a bare ``aiswmm login`` targets the current default
    # provider. Future flags add entries to ``_LOGIN_HANDLERS``.
    if getattr(args, "openai", False):
        provider = "openai"
    elif getattr(args, "anthropic", False):
        provider = "anthropic"
    else:
        provider = _current_default_provider()
    handler = _LOGIN_HANDLERS.get(provider)
    if handler is None:
        print(
            f"error: no login handler for provider {provider!r}. "
            f"Supported: {', '.join(sorted(_LOGIN_HANDLERS))}.",
            file=sys.stderr,
        )
        return 1
    return handler(args)


def _current_default_provider() -> str:
    """Return ``provider.default`` from config, or :data:`DEFAULT_PROVIDER`.

    Unknown / stale values (e.g. a legacy provider name) fall back to
    the shipped default so a bare ``aiswmm login`` always lands on a
    handler we can run.
    """
    default = str(load_config().get("provider.default", DEFAULT_PROVIDER))
    return default if default in _PROVIDER_SPECS else DEFAULT_PROVIDER


# ---------------------------------------------------------------------------
# Key-store handlers
# ---------------------------------------------------------------------------


def _make_key_handler(provider: str):
    """Build the login handler that stores ``provider``'s API key.

    The handler reads the key without echo (``getpass``) unless supplied
    via the provider's ``AISWMM_LOGIN_*_KEY`` env var (used by tests /
    non-interactive automation), writes it to ``~/.aiswmm/env`` at mode
    0600, sets ``provider.default`` + the provider's model default, and
    never prints the key back.
    """
    spec = _PROVIDER_SPECS[provider]

    def _handler(args: argparse.Namespace) -> int:
        key = os.environ.get(spec["login_env"])
        if not key:
            try:
                key = getpass.getpass(f"{spec['label']} API key (input hidden): ")
            except (EOFError, KeyboardInterrupt):
                print("\nAborted; no key stored.", file=sys.stderr)
                return 1
        key = (key or "").strip()
        if not key:
            print("error: empty API key; nothing stored.", file=sys.stderr)
            return 1

        env_path = _write_key_to_env(spec["env_var"], key)
        set_config_value("provider.default", provider)
        set_config_value(spec["model_key"], spec["model_default"])
        print(f"Stored {spec['label']} API key in {env_path} (mode 0600).")
        print(
            f"Default provider set to {provider}; "
            f"{spec['model_key']} set to {spec['model_default']}."
        )
        print(
            f"Note: source the env file (or restart your shell) so "
            f"{spec['env_var']} is exported."
        )
        return 0

    return _handler


def _write_key_to_env(var_name: str, key: str) -> Path:
    """Write/replace the ``var_name`` line in ``~/.aiswmm/env`` (0600).

    Preserves any other lines already in the file; only the ``var_name``
    export is rewritten. The file is created with restrictive
    permissions and re-chmod'd on every write so an existing loose-mode
    file is tightened.
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
                if name == var_name:
                    continue  # drop the old key line; we rewrite it below
                lines.append(raw)
        except OSError:
            lines = []

    lines.append(f'export {var_name}="{key}"')
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
    """Print the current auth state — default provider + key presence."""
    default_provider = str(load_config().get("provider.default", DEFAULT_PROVIDER))
    openai_key = _provider_key_present("openai")
    anthropic_key = _provider_key_present("anthropic")

    print(f"default provider:           {default_provider}")
    print(f"OpenAI API key present:     {'yes' if openai_key else 'no'}")
    print(f"Anthropic API key present:  {'yes' if anthropic_key else 'no'}")
    return 0


# ---------------------------------------------------------------------------
# Shared probes (no secret material crosses these)
# ---------------------------------------------------------------------------


def _provider_key_present(provider: str) -> bool:
    """Reuse the preflight's env / env-file / config key detection."""
    from agentic_swmm.agent.provider_preflight import provider_key_present

    return provider_key_present(provider)


# Registry: provider name -> login handler. Adding a backend's login
# method is a one-line entry here plus a ``_PROVIDER_SPECS`` entry.
_LOGIN_HANDLERS = {
    "openai": _make_key_handler("openai"),
    "anthropic": _make_key_handler("anthropic"),
}


__all__ = ["register", "main"]
