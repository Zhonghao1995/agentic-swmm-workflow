"""Interactive onboarding wizard for ``aiswmm setup`` (ADR-0008).

Detection-first, menu-second (the pattern OpenClaw-class onboarding
proved out): probe what is already there (exported keys, a running
Ollama / LM Studio, a local OpenAI-compatible gateway), then show the
route menu with detection badges, then the model menu (live listings
for local endpoints), then only the prompts that are actually needed
(key / base URL), then an optional local fallback and an optional live
verification ping.

Everything IO is injected (``ask`` / ``ask_secret`` / ``print_fn`` /
``probe_json``) so tests drive the wizard headlessly; the module never
calls ``input()`` or the network directly except through those seams.
The wizard *decides*; persisting the outcome (config writes, key
storage) stays in ``commands/setup.py`` so the write path is one place.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from agentic_swmm.agent.provider_preflight import provider_key_value
from agentic_swmm.providers.routes import (
    ROUTES,
    WIRE_ANTHROPIC_MESSAGES,
    RouteSpec,
    resolved_base_url,
)

# The codex route's gateway candidates, probed in order. CLIProxyAPI
# serves :8317, OmniRoute :20128; both expose GET /v1/models.
GATEWAY_CANDIDATES = (
    "http://localhost:8317/v1",
    "http://localhost:20128/v1",
)

_PROBE_TIMEOUT_S = 0.8
_VERIFY_TIMEOUT_S = 6.0

# Routes we offer a local fallback for (remote primaries only).
_LOCAL_ROUTES = frozenset({"ollama", "lmstudio"})


@dataclass(frozen=True)
class WizardResult:
    """What the user chose; ``commands/setup.py`` persists it."""

    route: str
    model: str
    api_key: str  # "" = nothing to store
    base_url: str  # "" = keep the route default
    fallback: str  # "" = none
    verified: bool | None  # None = verification skipped/unavailable


def probe_json(url: str, *, timeout: float = _PROBE_TIMEOUT_S, headers: dict[str, str] | None = None) -> Any | None:
    """GET ``url`` and parse JSON; ``None`` on any failure (probe, not call)."""
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _models_from_listing(payload: Any) -> list[str]:
    """Extract model ids from either an OpenAI ``/v1/models`` or an
    Ollama ``/api/tags`` payload."""
    if not isinstance(payload, dict):
        return []
    models: list[str] = []
    for item in payload.get("data") or []:  # OpenAI-compatible listing
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            models.append(item["id"])
    for item in payload.get("models") or []:  # Ollama /api/tags
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            models.append(item["name"])
    return models


@dataclass(frozen=True)
class RouteDetection:
    """Liveness/credential facts the menu shows as badges."""

    key_found: bool
    alive: bool | None  # None = route has no local endpoint to probe
    live_models: tuple[str, ...]
    gateway_base_url: str  # codex only: which candidate answered


def detect_route(spec: RouteSpec, probe: Callable[..., Any | None]) -> RouteDetection:
    key_found = bool(spec.key_env) and provider_key_value(spec.name) is not None
    if spec.name == "codex":
        for base in GATEWAY_CANDIDATES:
            payload = probe(f"{base}/models")
            if payload is not None:
                return RouteDetection(
                    key_found=key_found,
                    alive=True,
                    live_models=tuple(_models_from_listing(payload)),
                    gateway_base_url=base,
                )
        return RouteDetection(key_found=key_found, alive=False, live_models=(), gateway_base_url="")
    if spec.detect_url:
        payload = probe(spec.detect_url)
        return RouteDetection(
            key_found=key_found,
            alive=payload is not None,
            live_models=tuple(_models_from_listing(payload)),
            gateway_base_url="",
        )
    return RouteDetection(key_found=key_found, alive=None, live_models=(), gateway_base_url="")


def _badge(spec: RouteSpec, det: RouteDetection) -> str:
    parts: list[str] = []
    if det.alive is True:
        parts.append(f"running, {len(det.live_models)} model(s)" if det.live_models else "running")
    elif det.alive is False:
        parts.append("not detected")
    if spec.key_env:
        if det.key_found:
            parts.append("key: found")
        elif not spec.keyless:
            parts.append("key: needed")
    if not parts and spec.keyless:
        parts.append("keyless")
    return ", ".join(parts)


# Printed only when the user declines the managed install or it fails. The
# binary name differs by install method (the Homebrew formula links
# `cliproxyapi`; the GitHub release ships `cli-proxy-api`), which is exactly
# the kind of detail `aiswmm gateway install` exists to absorb.
_GATEWAY_RECIPE = (
    "No local gateway detected on :8317 (CLIProxyAPI) or :20128 (OmniRoute).\n"
    "The codex route talks to any local OpenAI-compatible gateway that fronts\n"
    "your ChatGPT subscription. Set one up with either:\n"
    "  aiswmm gateway install   (any platform; pinned CLIProxyAPI build)\n"
    "  brew install cliproxyapi && cliproxyapi -codex-login      (macOS)\n"
    "  npm install -g omniroute && omniroute                     (needs Node 22+)\n"
    "Your selection is saved either way; start the gateway before the first run."
)


def _offer_gateway(ask, print_fn, install, login) -> bool:
    """Install the local gateway the codex route needs, and sign in.

    The wizard used to print a shell recipe and stop, which left the only
    keyless-with-a-real-model route reachable solely by users who already
    knew what a gateway was. Stopping at "installed" was barely better: it
    handed back two more commands and then ran a connection test that could
    not pass yet. ``install`` and ``login`` are injected so this stays
    testable without a network or a browser.
    """
    print_fn("The codex route needs a local gateway that fronts your ChatGPT plan.")
    print_fn("aiswmm can install a pinned CLIProxyAPI build (MIT) under ~/.aiswmm/gateway/.")
    if ask("Install it now? [Y/n]: ").strip().lower() in ("n", "no"):
        return False
    try:
        result = install()
    except Exception as exc:  # any failure falls back to the printed recipe
        print_fn(f"Gateway install failed: {exc}")
        return False
    print_fn(f"Installed -> {result.path}")
    if ask("Sign in to ChatGPT now? [Y/n]: ").strip().lower() in ("n", "no"):
        print_fn("Later, run:  aiswmm gateway login    (signs in, then serves)")
        return True
    if login() != 0:
        print_fn("Sign-in did not finish. Run `aiswmm gateway login` when ready.")
    return True


def run_wizard(
    *,
    ask: Callable[[str], str],
    ask_secret: Callable[[str], str],
    print_fn: Callable[[str], None] = print,
    probe: Callable[..., Any | None] = probe_json,
    verify: Callable[[RouteSpec, str, str], tuple[bool, str]] | None = None,
    install_gateway: Callable[[], Any] | None = None,
    gateway_login: Callable[[], int] | None = None,
) -> WizardResult | None:
    """Drive the interactive flow; return ``None`` when the user aborts.

    ``install_gateway`` and ``gateway_login`` are NOT defaulted to the real
    implementations here. They download 58 MB and open a browser OAuth flow
    that writes vendor credentials, and a caller that has not opted in must
    never trigger either by omission: the wizard falls back to printing the
    manual recipe. ``commands/setup.py`` wires both explicitly.
    """

    try:
        return _run(
            ask=ask,
            ask_secret=ask_secret,
            print_fn=print_fn,
            probe=probe,
            verify=verify,
            install_gateway=install_gateway,
            gateway_login=gateway_login,
        )
    except (EOFError, KeyboardInterrupt):
        print_fn("")
        print_fn("Setup wizard aborted; nothing was written.")
        print_fn("Non-interactive path: aiswmm setup --provider <route> [--model <id>]")
        return None


def _run(*, ask, ask_secret, print_fn, probe, verify, install_gateway, gateway_login) -> WizardResult | None:
    print_fn("Agentic SWMM setup — LLM route")
    print_fn("")

    specs = list(ROUTES.values())
    detections = {spec.name: detect_route(spec, probe) for spec in specs}

    for index, spec in enumerate(specs, start=1):
        badge = _badge(spec, detections[spec.name])
        badge_text = f"  [{badge}]" if badge else ""
        print_fn(f"  {index:>2}. {spec.name:<11} {spec.label}{badge_text}")
    print_fn("")

    route = _choose_route(ask, print_fn, specs)
    if route is None:
        return None
    spec = ROUTES[route]
    det = detections[route]

    if route == "codex" and det.alive is False:
        print_fn("")
        managed = install_gateway is not None and gateway_login is not None
        if not managed or not _offer_gateway(ask, print_fn, install_gateway, gateway_login):
            print_fn(_GATEWAY_RECIPE)
        print_fn("")

    base_url = det.gateway_base_url if det.gateway_base_url not in ("", spec.base_url.rstrip("/")) else ""
    if not spec.base_url:
        raw = ask("Base URL for the endpoint (e.g. http://host:8000/v1): ").strip()
        if not raw:
            print_fn("A custom endpoint needs a base URL; aborting without writing.")
            return None
        base_url = raw.rstrip("/")

    model = _choose_model(ask, print_fn, spec, det)
    if not model:
        print_fn("A model id is required; aborting without writing.")
        return None

    api_key = _collect_key(ask_secret, print_fn, spec, det)
    if api_key is None:
        return None

    fallback = _offer_fallback(ask, print_fn, route, detections)

    verified: bool | None = None
    effective_base = base_url or resolved_base_url(route)
    verify_fn = verify or verify_route
    answer = ask("Test the connection now? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        stored_key = api_key or provider_key_value(route) or ""
        ok, detail = verify_fn(spec, effective_base, stored_key)
        verified = ok
        if ok:
            print_fn(f"Connection OK: {detail}")
        else:
            print_fn(f"warning: verification failed: {detail}")
            print_fn(f"Saved anyway; fix later with `aiswmm login {route}` and `aiswmm doctor`.")

    return WizardResult(
        route=route,
        model=model,
        api_key=api_key,
        base_url=base_url,
        fallback=fallback,
        verified=verified,
    )


def _choose_route(ask, print_fn, specs: list[RouteSpec]) -> str | None:
    names = [spec.name for spec in specs]
    while True:
        raw = ask(f"Select route (number or name) [{names[0]}]: ").strip()
        if not raw:
            return names[0]
        if raw in names:
            return raw
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return names[int(raw) - 1]
        print_fn(f"Not a route: {raw!r}. Enter 1-{len(names)} or a route name.")


def _choose_model(ask, print_fn, spec: RouteSpec, det: RouteDetection) -> str:
    menu = list(det.live_models) or list(spec.model_menu)
    default = spec.default_model or (menu[0] if menu else "")
    if menu:
        print_fn("")
        print_fn(f"Models for {spec.name} (Enter for default, a number, or any model id):")
        for index, model_id in enumerate(menu[:12], start=1):
            marker = " (default)" if model_id == default else ""
            print_fn(f"  {index:>2}. {model_id}{marker}")
    prompt = f"Model [{default}]: " if default else "Model id: "
    raw = ask(prompt).strip()
    if not raw:
        return default
    if raw.isdigit() and menu and 1 <= int(raw) <= min(len(menu), 12):
        return menu[int(raw) - 1]
    return raw


def _collect_key(ask_secret, print_fn, spec: RouteSpec, det: RouteDetection) -> str | None:
    """Return the key to store ("" = nothing), or ``None`` on abort."""
    if not spec.key_env:
        return ""
    if det.key_found:
        print_fn(f"Using the {spec.key_env} already present (environment or stored login).")
        return ""
    optional = spec.keyless
    suffix = " (optional, Enter to skip)" if optional else ""
    key = ask_secret(f"{spec.label} API key{suffix} (input hidden): ").strip()
    if not key and not optional:
        print_fn("An API key is required for this route; aborting without writing.")
        return None
    return key


def _offer_fallback(ask, print_fn, route: str, detections: dict[str, RouteDetection]) -> str:
    if route in _LOCAL_ROUTES:
        return ""
    ollama = detections.get("ollama")
    if ollama is None or ollama.alive is not True:
        return ""
    count = len(ollama.live_models)
    print_fn("")
    print_fn(f"Ollama is running locally ({count} model(s)).")
    answer = ask("Use it as an automatic local fallback when the primary fails? [y/N]: ")
    if answer.strip().lower() in ("y", "yes"):
        return "ollama"
    return ""


def verify_route(spec: RouteSpec, base_url: str, api_key: str) -> tuple[bool, str]:
    """Cheap live check: list models on the effective endpoint.

    Every wire in the table serves a models listing: OpenAI-compatible
    endpoints (both wires) at ``GET <base>/models`` with a Bearer key,
    Anthropic at ``GET <base>/models`` with ``x-api-key``. Returns
    ``(ok, human detail)``; never raises.
    """
    headers: dict[str, str] = {}
    if spec.wire == WIRE_ANTHROPIC_MESSAGES:
        headers["x-api-key"] = api_key or ""
        headers["anthropic-version"] = "2023-06-01"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{base_url.rstrip('/')}/models"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_VERIFY_TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} from {url}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"{url} unreachable ({getattr(exc, 'reason', exc)})"
    count = len(_models_from_listing(payload))
    return True, f"{url} answered ({count} model(s) listed)"


__all__ = [
    "GATEWAY_CANDIDATES",
    "RouteDetection",
    "WizardResult",
    "detect_route",
    "probe_json",
    "run_wizard",
    "verify_route",
]
