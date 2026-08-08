"""``aiswmm login`` — store credentials/settings for any provider route.

Login is a thin dispatcher over a ``route -> handler`` registry
(:data:`_LOGIN_HANDLERS`) derived from the route table
(:mod:`agentic_swmm.providers.routes`, ADR-0008). Adding a route there
adds its login surface here automatically.

Surfaces:

* ``aiswmm login`` (default) — configure the *current default* route
  (``provider.default``, normally ``openai``).
* ``aiswmm login <route>`` — configure that route and make it the
  default. Keyed routes prompt for their API key; keyless local routes
  (ollama, lmstudio) just select themselves; gateway/custom routes
  treat the key as optional and ``custom`` also asks for its base URL
  and model.
* ``aiswmm login --openai`` / ``--anthropic`` — legacy aliases for the
  two original routes (kept working forever).
* ``aiswmm login --status`` — print the default/fallback routes and
  per-route credential state. No secrets.

Keys are written to ``~/.aiswmm/env`` (file mode 0600) and never
echoed. The handlers never print or log credential material. Every
interactive prompt has a non-interactive escape hatch for automation:
``AISWMM_LOGIN_<ROUTE>_KEY`` / ``_BASE_URL`` / ``_MODEL``.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.config import (
    DEFAULT_PROVIDER,
    config_dir,
    load_config,
    set_config_value,
)
from agentic_swmm.providers.routes import ROUTES, RouteSpec, resolved_model, route_names


def _login_env(route: str, suffix: str = "KEY") -> str:
    """Automation env var for a prompt: ``AISWMM_LOGIN_<ROUTE>_<SUFFIX>``."""
    return f"AISWMM_LOGIN_{route.upper().replace('-', '_')}_{suffix}"


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "login",
        help="Store credentials for a provider route (OpenAI by default).",
    )
    parser.add_argument(
        "route",
        nargs="?",
        choices=route_names(),
        default=None,
        help="Provider route to configure (default: the current default route).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--openai",
        action="store_true",
        help="Store an OpenAI API key and set OpenAI as the default route.",
    )
    group.add_argument(
        "--anthropic",
        action="store_true",
        help="Store an Anthropic API key and set Anthropic as the default route.",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Print the current auth state for every route (no secrets) and exit.",
    )
    register_example_flag(parser, example_text="aiswmm login")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    if getattr(args, "status", False):
        return _login_status(args)
    # Resolve which route to configure. Legacy flags are explicit; then
    # the positional route; a bare ``aiswmm login`` targets the current
    # default route.
    if getattr(args, "openai", False):
        route = "openai"
    elif getattr(args, "anthropic", False):
        route = "anthropic"
    else:
        route = getattr(args, "route", None) or _current_default_provider()
    handler = _LOGIN_HANDLERS.get(route)
    if handler is None:
        print(
            f"error: no login handler for provider route {route!r}. "
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
    return default if default in ROUTES else DEFAULT_PROVIDER


# ---------------------------------------------------------------------------
# Prompt helpers (each with an env escape hatch for automation/tests)
# ---------------------------------------------------------------------------


def _prompt_secret(route: str, label: str, *, optional: bool) -> str | None:
    """Read a key without echo; ``None`` means aborted (keyed routes only)."""
    key = os.environ.get(_login_env(route))
    if key is None:
        suffix = " (optional, Enter to skip)" if optional else ""
        try:
            key = getpass.getpass(f"{label} API key{suffix} (input hidden): ")
        except (EOFError, KeyboardInterrupt):
            if optional:
                return ""
            print("\nAborted; no key stored.", file=sys.stderr)
            return None
    return (key or "").strip()


def _prompt_value(route: str, suffix: str, question: str) -> str:
    """Read a non-secret value (base URL, model id) with env escape hatch."""
    value = os.environ.get(_login_env(route, suffix))
    if value is None:
        try:
            value = input(question)
        except (EOFError, KeyboardInterrupt):
            value = ""
    return (value or "").strip()


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


def _make_route_handler(route: str):
    """Build the login handler for ``route`` from its RouteSpec.

    Keyed routes require a key; keyless routes with a ``key_env``
    (codex, custom) treat it as optional; fully keyless routes (ollama,
    lmstudio) skip the key step. ``custom`` additionally requires a
    base URL and a model id since the table ships neither.
    """
    spec: RouteSpec = ROUTES[route]

    def _handler(args: argparse.Namespace) -> int:
        key = ""
        if spec.key_env:
            optional = spec.keyless
            got = _prompt_secret(route, spec.label, optional=optional)
            if got is None:
                return 1
            key = got
            if not key and not optional:
                print("error: empty API key; nothing stored.", file=sys.stderr)
                return 1

        base_url = ""
        if not spec.base_url:
            base_url = _prompt_value(
                route, "BASE_URL", f"{spec.label} base URL (e.g. http://host:port/v1): "
            )
            if not base_url:
                print("error: this route needs a base URL; nothing stored.", file=sys.stderr)
                return 1

        model = spec.default_model
        if not model:
            model = _prompt_value(route, "MODEL", f"{spec.label} model id: ")
            if not model:
                print("error: this route needs a model id; nothing stored.", file=sys.stderr)
                return 1

        if key:
            env_path = _write_key_to_env(spec.key_env, key)
            print(f"Stored {spec.label} API key in {env_path} (mode 0600).")
        elif spec.key_env and spec.keyless:
            print(f"No key stored for {route} (optional for this route).")

        if base_url:
            set_config_value(f"{route}.base_url", base_url)
            print(f"{route}.base_url set to {base_url}.")
        set_config_value("provider.default", route)
        set_config_value(f"{route}.model", model)
        print(f"Default provider route set to {route}; {route}.model set to {model}.")
        if spec.hint:
            print(f"Note: {spec.hint}")
        if key:
            print(
                f"Note: source the env file (or restart your shell) so "
                f"{spec.key_env} is exported."
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
    """Print the current auth state — default/fallback + per-route lines."""
    config = load_config()
    default_provider = str(config.get("provider.default", DEFAULT_PROVIDER))
    fallback = str(config.get("provider.fallback", "") or "")
    openai_key = _provider_key_present("openai")
    anthropic_key = _provider_key_present("anthropic")

    print(f"default provider:           {default_provider}")
    if fallback:
        print(f"fallback provider:          {fallback}")
    print(f"OpenAI API key present:     {'yes' if openai_key else 'no'}")
    print(f"Anthropic API key present:  {'yes' if anthropic_key else 'no'}")
    print()
    print("routes:")
    for name, spec in ROUTES.items():
        marker = "*" if name == default_provider else ("f" if name == fallback else " ")
        cred = _credential_state(name, spec)
        model = resolved_model(name, config.get) or "(unset)"
        print(f"  {marker} {name:<11} {cred:<18} model: {model}")
    print()
    print("  * = default, f = fallback. `aiswmm login <route>` configures one;")
    print("  `aiswmm setup` runs the interactive picker.")
    return 0


def _credential_state(name: str, spec: RouteSpec) -> str:
    if not spec.key_env:
        return "keyless"
    present = _provider_key_present_keyed(name)
    if spec.keyless:
        return "key: optional/set" if present else "key: optional"
    return "key: present" if present else "key: MISSING"


# ---------------------------------------------------------------------------
# Shared probes (no secret material crosses these)
# ---------------------------------------------------------------------------


def _provider_key_present(provider: str) -> bool:
    """Reuse the preflight's env / env-file / config key detection."""
    from agentic_swmm.agent.provider_preflight import provider_key_present

    return provider_key_present(provider)


def _provider_key_present_keyed(provider: str) -> bool:
    """Key presence ignoring the keyless shortcut (status display only)."""
    from agentic_swmm.agent.provider_preflight import provider_key_value

    return provider_key_value(provider) is not None


# Registry: route name -> login handler, derived from the route table.
# Adding a route in routes.py adds its login surface automatically.
_LOGIN_HANDLERS = {name: _make_route_handler(name) for name in ROUTES}


__all__ = ["register", "main"]
