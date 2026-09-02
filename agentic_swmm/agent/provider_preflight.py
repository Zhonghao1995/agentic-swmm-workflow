"""Provider preflight — route/API-key selection for the interactive shell.

A first-run user typing ``aiswmm`` with no args lands in the
interactive shell that runs the LLM planner. The shell prints the
welcome banner, accepts a prompt, and then drives the planner. This
module is the boot-time diagnostic: before the shell hands control to
the planner we resolve *which* provider route to use and whether its
API key is detectable.

Since ADR-0008 the set of routes comes from
:mod:`agentic_swmm.providers.routes` (openai, anthropic, codex,
openrouter, deepseek, groq, gemini, ollama, lmstudio, custom). The
resolved default is ``provider.default`` from ``~/.aiswmm/config.toml``
when set, else :data:`DEFAULT_PROVIDER`. A keyed route is *configured*
when its key is reachable from any of three tiers: the environment,
``~/.aiswmm/env``, or the ``[<route>]`` section of
``~/.aiswmm/config.toml``. Keyless routes (ollama, lmstudio; codex and
custom accept an optional key) count as configured with no key stored.

We deliberately do *not* validate the key (no network call) — the goal
is to pick the right backend and fail loud only when the selected
route has no detectable key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agentic_swmm.config import DEFAULT_PROVIDER
from agentic_swmm.providers.routes import ROUTES, route_names


# Env-var name carrying each keyed route's API key. Derived from the
# route table (single source of truth) so the detection tiers, doctor,
# login, and the factory all agree. Keyless routes with no key env are
# absent by construction.
_PROVIDER_KEY_ENV = {
    name: spec.key_env for name, spec in ROUTES.items() if spec.key_env
}


def _guidance_missing_key(route_name: str) -> str:
    """The soft warning shown when the selected keyed route has no key."""
    key_env = _PROVIDER_KEY_ENV.get(route_name, "the route's API key")
    return (
        f"No API key detected for the selected provider route '{route_name}'.\n"
        "\n"
        f"  run `aiswmm login {route_name}` to store a key, or\n"
        f'  export {key_env}="...".\n'
        "\n"
        "Run `aiswmm setup` for the interactive picker (all routes, local\n"
        "models included).\n"
        "\n"
        "Continuing with the LLM planner; the first prompt will fail until a\n"
        "key for the selected route is set."
    )


_GUIDANCE_NOTHING_CONFIGURED = (
    "No LLM provider route configured.\n"
    "\n"
    "Run `aiswmm setup` for the interactive picker, or `aiswmm login` to\n"
    "store a key for the default route (openai).\n"
    "\n"
    f"Known routes: {', '.join(route_names())}.\n"
    "\n"
    "Continuing in rule-planner mode (no LLM, limited verbs available)."
)


@dataclass(frozen=True)
class ProviderPreflightResult:
    """Outcome of :func:`check_interactive_provider`.

    ``provider_name`` is the resolved route name when one was selected;
    ``None`` only in the safety-net case where the resolved default is
    an unknown route. ``fallback_planner`` is what the CLI should
    dispatch when the user-supplied planner is unavailable (always
    ``"rule"`` today). ``guidance_message`` is the multi-line block the
    caller writes to stderr: empty when the selected route is ready, a
    soft warning when it is selected without a detectable key, and the
    full no-provider block in the rule-fallback safety net.
    """

    has_configured_provider: bool
    provider_name: str | None
    fallback_planner: str
    guidance_message: str


def _aiswmm_env_path() -> Path:
    """Return the env file ``aiswmm login`` / ``aiswmm setup`` write.

    ``$AISWMM_CONFIG_DIR/env`` when the config dir is redirected, else
    ``~/.aiswmm/env``. Resolved through :func:`config_dir` so the reader
    and the writer (``login._write_key_to_env``) can never name two
    different files.
    """
    from agentic_swmm.config import config_dir

    return config_dir() / "env"


def _aiswmm_config_path() -> Path:
    """Return ``config.toml`` in the same directory as the env file."""
    from agentic_swmm.config import config_path

    return config_path()


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


def stored_env_value(var_name: str) -> str | None:
    """Return ``var_name`` from the environment, else from the env file.

    The two tiers every persisted setting shares: a shell ``export`` wins
    for one session, and the file ``aiswmm login`` / ``aiswmm setup``
    write is the durable fallback. API keys add a third tier
    (``config.toml``) on top in :func:`provider_key_value`; plain settings
    such as ``AISWMM_SWMMCANADA_URL`` stop here. Before this resolver
    existed those settings were read from ``os.environ`` alone, so a
    setup-enabled install was "not configured" in every shell that did
    not source the file (live finding F-01, 2026-09-02).
    """
    from_env = os.environ.get(var_name, "").strip()
    if from_env:
        return from_env
    return _env_file_key_value(_aiswmm_env_path(), var_name)


def provider_key_value(provider_name: str) -> str | None:
    """Return ``provider_name``'s API key, or ``None`` when unreachable.

    This is the resolver the runtime and the diagnostics share. Before it
    existed, `aiswmm login` wrote ``~/.aiswmm/env`` and the providers read
    only ``os.environ``, so a fully onboarded user was told the key was
    present and then handed "<VAR> is not set" by the runtime.

    Precedence, highest first:

    1. the route's environment variable, so a shell ``export``
       overrides stored state for one session without editing files;
    2. ``~/.aiswmm/env``, where ``aiswmm login`` writes;
    3. the ``[<route>]`` section of ``~/.aiswmm/config.toml``.

    Routes with no key mapping (fully keyless: ollama, lmstudio) and
    unknown route names return ``None``.
    """
    var_name = _PROVIDER_KEY_ENV.get(provider_name)
    if not var_name:
        return None
    stored = stored_env_value(var_name)
    if stored:
        return stored
    return _config_file_key_value(_aiswmm_config_path(), provider_name)


def provider_key_present(provider_name: str) -> bool:
    """Return True when ``provider_name`` is usable credential-wise.

    Keyed routes derive from :func:`provider_key_value` rather than
    re-deriving presence on their own: what ``doctor`` reports is then,
    by construction, an answer about the very value the runtime will
    use. Keyless routes (``spec.keyless``) are always usable — a stored
    key is optional there and its absence is not a finding.
    """
    spec = ROUTES.get(provider_name)
    if spec is not None and spec.keyless:
        return True
    return provider_key_value(provider_name) is not None


def _openai_key_present() -> bool:
    """Back-compat shim: True when an OpenAI key is reachable.

    Retained because the login ``--status`` surface imports this name.
    """
    return provider_key_present("openai")


def check_interactive_provider() -> ProviderPreflightResult:
    """Resolve the interactive planner route from config + key tiers.

    Resolution order:

    1. The resolved default is the configured ``provider.default`` when
       set, else :data:`DEFAULT_PROVIDER` (``openai``).
    2. When the resolved default is a known route it is selected. We
       keep the LLM planner even if no key is detected — the provider
       authenticates at call time and a user who just exported a key in
       another shell must not be dropped to the rule planner. When the
       selected keyed route has no detectable key we attach a soft
       warning (`aiswmm login <route>` / `aiswmm setup` hints).
    3. Safety net: if the resolved default is some unknown route, we
       fall back to the rule planner with the full no-provider guidance.
    """
    explicit_default = _config_default_provider(_aiswmm_config_path())
    resolved_default = explicit_default or DEFAULT_PROVIDER

    if resolved_default in ROUTES:
        key_present = provider_key_present(resolved_default)
        guidance = "" if key_present else _guidance_missing_key(resolved_default)
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
    "provider_key_value",
]
