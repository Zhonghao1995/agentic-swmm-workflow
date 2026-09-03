"""Check the model a route pins against what its gateway offers.

Live test 2026-09-03 (S38): the codex route pins ``gpt-5.6-sol``; the local
gateway stopped offering it while still offering two menu siblings, every
session failed with a raw HTTP 404 ``model_not_found``, and ``aiswmm doctor``
kept reporting the route "ready" because it only checked the key. This
module lists what the gateway offers (with the route's key) and reconciles
the configured model against that list; callers decide what to print.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from agentic_swmm.providers.routes import ROUTES, RouteSpec

Probe = Callable[[str, dict[str, str], float], Any | None]


def _probe_json(url: str, headers: dict[str, str], timeout: float) -> Any | None:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def models_from_listing(payload: Any) -> list[str]:
    """Return the model ids of an OpenAI-style ``/models`` listing."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            out.append(item["id"])
    return out


def offered_models(
    spec: RouteSpec,
    *,
    key: str | None = None,
    probe: Probe = _probe_json,
    timeout: float = 3.0,
) -> tuple[str, ...] | None:
    """What the route's gateway offers, or ``None`` when it cannot be asked.

    Only routes with a ``detect_url`` are probed. The route's key goes in
    the bearer header: a keyless probe of the codex gateway returns an
    error object, not the listing (that read as "0 models" in the live
    test until the key was sent).
    """
    if not spec.detect_url:
        return None
    headers: dict[str, str] = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = probe(spec.detect_url, headers, timeout)
    if payload is None:
        return None
    if isinstance(payload, dict) and payload.get("error") and not payload.get("data"):
        return None
    return tuple(models_from_listing(payload))


def reconcile_model(
    spec: RouteSpec, configured: str | None, offered: tuple[str, ...] | None
) -> tuple[str | None, str | None]:
    """Return ``(model_to_use, note)``.

    * gateway not asked or empty listing: keep the configured model, no note;
    * configured model offered: keep it, no note;
    * otherwise: the first menu sibling the gateway offers, else the first
      offered model, with a one-line note naming the swap and the remedy.
    """
    if not offered or not configured or configured in offered:
        return configured, None
    replacement = next((m for m in spec.model_menu if m in offered), offered[0])
    shown = ", ".join(offered[:6]) + (", ..." if len(offered) > 6 else "")
    note = (
        f"{configured} is not offered by the {spec.name} gateway; using {replacement} "
        f"for this session (offered: {shown}). Run `aiswmm setup` to pin one."
    )
    return replacement, note


def model_not_found_hint(status: int, detail: str) -> str:
    """The remedy to append to an HTTP error that names a missing model."""
    if status == 404 and "model_not_found" in detail:
        return " The configured model is not offered by this route; run `aiswmm setup` to pick an offered model."
    return ""


def route_spec(name: str) -> RouteSpec | None:
    return ROUTES.get(name)


__all__ = [
    "models_from_listing",
    "model_not_found_hint",
    "offered_models",
    "reconcile_model",
    "route_spec",
]
