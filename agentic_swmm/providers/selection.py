"""The one provider/model resolution seam (ADR-0009).

Before this module, the idiom ``config.get("provider.default", DEFAULT)``
plus ``config.get(f"{p}.model")`` was hand-rolled at 14 call sites
(runtime loop, single-shot header, welcome banner, gap-fill, doctor,
login, model command). Each could drift, and none knew about the
ADR-0008 route table, so a user who set ``provider.default = groq`` in
config without writing ``groq.model`` got "explicit model required"
instead of the route's shipped default.

``resolve_selection`` is that idiom, once: explicit argument > config >
route-table default (via :func:`agentic_swmm.providers.routes.resolved_model`,
which also honours ``AISWMM_<ROUTE>_MODEL``). Unknown/stale route names
resolve with a best-effort model so the caller's existing error surface
(factory / SUPPORTED_PROVIDERS check) stays the one that reports them.
Defensive: any config-read failure degrades to the shipped defaults
rather than raising, matching the sites it replaces.
"""
from __future__ import annotations

from dataclasses import dataclass

from agentic_swmm.config import DEFAULT_PROVIDER, load_config
from agentic_swmm.providers.routes import ROUTES, resolved_model


@dataclass(frozen=True)
class Selection:
    """The resolved (route, model) pair every consumer shares."""

    route: str
    model: str | None


def resolve_selection(
    explicit_provider: str | None = None,
    explicit_model: str | None = None,
) -> Selection:
    """Resolve the active route and model.

    Precedence per field: the explicit argument (CLI flag), then config
    (``provider.default`` / ``[<route>] model``), then the route table's
    default (which itself honours the ``AISWMM_<ROUTE>_MODEL`` env
    override). ``model`` may be ``None`` only for routes that ship no
    default (``custom``, ``lmstudio``) with nothing configured.
    """
    try:
        config_get = load_config().get
    except Exception:  # pragma: no cover - defensive; config is shallow
        def config_get(key: str, default=None):  # type: ignore[misc]
            return default

    route = (explicit_provider or "").strip() or str(
        config_get("provider.default", DEFAULT_PROVIDER) or DEFAULT_PROVIDER
    )

    explicit = (explicit_model or "").strip()
    if explicit:
        model: str | None = explicit
    elif route in ROUTES:
        model = resolved_model(route, config_get) or None
    else:
        raw = config_get(f"{route}.model", None)
        model = str(raw) if raw else None
    return Selection(route=route, model=model)


__all__ = ["Selection", "resolve_selection"]
