"""SWMMCanada typed-tool handler (ADR-0001).

Family: ``swmm-canada`` (upstream real-pipe INP source).

Surfaces the ``fetch_swmm_from_canada`` tool — a typed wrapper around
``agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi`` that the
LLM can pick directly from the tool registry. Unlike the swmm-anywhere
handler (an in-process import of a Python library), this one drives an
external HTTP service over a configurable base URL
(``AISWMM_SWMMCANADA_URL``) — see CONTEXT.md §"INP sources".

    LLM → tool_registry → fetch_swmm_from_canada_tool →
                          fetch_from_aoi(...) → SWMMCanada HTTP service

Typed-param validation mirrors the OpenAI / Anthropic function-calling
shape: a malformed call fails with a fail-soft ``_failure(...)`` payload
rather than raising into the planner loop. ``CanadaFetchError`` stages
are mapped to actionable hints the same way the swmm-anywhere handler
maps ``SynthRunError`` stages.
"""
from __future__ import annotations

import json
from datetime import date
import sys
from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _inp_source_tool,
    _object,
    _timestamped_run_dir,
)
from agentic_swmm.agent.types import ToolCall, ToolSpec
from agentic_swmm.agent.ui import update_tool_status


#: The public SWMMCanada deployment. Named here because it was named nowhere:
#: the old hint offered "a local container at http://localhost:8000", so a
#: planner asked how to configure the route repeated that address to the user
#: as if it were real. It was not reading a service; it was reading this file.
HOSTED_SERVICE_URL = "https://swmm.h2ox.me"


def _stdin_is_tty() -> bool:
    """A consent question needs a human; tests force either answer."""
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def _offer_inline_enable() -> str | None:
    """Ask the person at the keyboard, once, whether to enable SWMMCanada.

    The setup wizard already owns this question (``upstream_optin.offer``
    names where the area goes, persists the answer to the env file and
    exports it for this process). Re-using it here means a user who
    skipped setup and then asked for a Canadian network gets the same
    one-time question in-flow instead of a dead end, and the planner
    never has to invent a URL. Headless callers get ``None`` and keep the
    fail-soft payload.
    """
    if not _stdin_is_tty():
        return None
    from agentic_swmm.agent import permissions
    from agentic_swmm.commands import upstream_optin

    permissions._prepare_prompt_line()
    try:
        return upstream_optin.offer(ask=input, print_fn=print)
    except EOFError:
        return None
    finally:
        permissions._restore_after_prompt()


def _stage_hint(stage: str) -> str:
    """Return an actionable hint for a ``CanadaFetchError`` stage tag."""
    if stage == "config_missing":
        return (
            "SWMMCanada is optional and stays off until the user enables it. "
            f"The public deployment is {HOSTED_SERVICE_URL}; enabling it sends the "
            "area the user requests to that service, so the choice is theirs: "
            "`aiswmm setup` asks once and saves the answer, or they export "
            f"AISWMM_SWMMCANADA_URL={HOSTED_SERVICE_URL} (or their own deployment). "
            "Tell the user how to enable it and stop. Do not pass base_url on "
            "the user's behalf unless they named a deployment themselves."
        )
    if stage == "task_failed":
        return (
            "the service rejected this AOI — SWMMCanada covers Canadian areas "
            "(real published storm networks for its supported cities, 35 at last "
            "sync, and synthesized models elsewhere in Canada). For regions "
            "outside Canada use synth_swmm_from_bbox instead."
        )
    if stage == "timeout":
        return (
            "the upstream build is still running after the 10-minute poll budget. "
            "The service build keeps running; its task id is in 00_raw/swmmcanada/task.json. Do not repeat the same AOI: it times out again and leaves another build "
            "running on the service (live test 2026-09-03, S40 r3). Pass city only for "
            "the 1 km default window (about 2 minutes), or a smaller bbox, or tell the "
            "user the requested area is too large for this budget."
        )
    if stage == "extract":
        return (
            "the downloaded swmm_model.zip was missing a .inp file or was "
            "corrupt. Check that the SWMMCanada service produced a complete "
            "model for this AOI (inspect the kept swmm_model.zip in the run dir)."
        )
    if stage in {"submit", "poll", "download"}:
        return (
            "the SWMMCanada service was unreachable or errored at the HTTP layer. "
            "Check AISWMM_SWMMCANADA_URL and that the service is healthy "
            "(GET /api/v1/healthz)."
        )
    return (
        "fetch_swmm_from_canada failed; check the SWMMCanada service URL and "
        "that the AOI falls within a supported Canadian city."
    )


# Canada's coarse WGS84 bounding box. Deliberately generous: a false pass
# (e.g. a northern-US border town) only costs one upstream call that the
# service rejects anyway, while nothing inside Canada is ever excluded —
# so a crude box is enough and no precise border polygon is needed.
_CANADA_LON = (-141.1, -52.5)
_CANADA_LAT = (41.6, 83.2)


def _aoi_centre_outside_canada(aoi_geojson: str) -> str | None:
    """Return an error message when the AOI centre is clearly outside Canada.

    Deterministic pre-check so an out-of-scope AOI fails soft before the
    submit+poll round-trip, steering the planner to synth_swmm_from_bbox
    instead of relying on tool-description judgement alone. Geometry the
    check can't read returns None — upstream stays the authority (ADR-0001),
    this never blocks anything it does not understand.
    """
    try:
        ring = json.loads(aoi_geojson)["coordinates"][0]
        lons = [float(point[0]) for point in ring]
        lats = [float(point[1]) for point in ring]
    except (ValueError, TypeError, KeyError, IndexError):
        return None
    if not lons or not lats:
        return None
    lon = (min(lons) + max(lons)) / 2
    lat = (min(lats) + max(lats)) / 2
    if _CANADA_LON[0] <= lon <= _CANADA_LON[1] and _CANADA_LAT[0] <= lat <= _CANADA_LAT[1]:
        return None
    return (
        f"AOI centre (lon {lon:.3f}, lat {lat:.3f}) is outside Canada — "
        "SWMMCanada builds models from Canadian open data only."
    )


def _bbox_to_polygon(bbox: list[float]) -> str:
    """Convert ``[min_lon, min_lat, max_lon, max_lat]`` to a closed GeoJSON polygon string."""
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
    ring = [
        [min_lon, min_lat],
        [max_lon, min_lat],
        [max_lon, max_lat],
        [min_lon, max_lat],
        [min_lon, min_lat],
    ]
    return json.dumps({"type": "Polygon", "coordinates": [ring]})


def _bbox_is_placeholder(bbox_raw: Any) -> bool:
    """True for a bbox the planner filled in to satisfy the schema, not to say anything.

    ``[0, 0, 0, 0]`` (live test 2026-09-03, S40 r2: sent next to
    ``city="Regina"`` and rejected as an AOI in the Gulf of Guinea) or any
    zero-area box. Such a bbox is treated as absent so ``city`` can act.
    """
    if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) != 4:
        return False
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox_raw)
    except (TypeError, ValueError):
        return False
    return (min_lon == max_lon) or (min_lat == max_lat)


def _resolve_aoi(call: ToolCall) -> tuple[str | None, str | None]:
    """Return ``(aoi_geojson, error)``. Accepts an explicit GeoJSON string or a bbox."""
    aoi_raw = call.args.get("aoi_geojson")
    if isinstance(aoi_raw, str) and aoi_raw.strip():
        return aoi_raw, None
    bbox_raw = call.args.get("bbox")
    if _bbox_is_placeholder(bbox_raw):
        city_raw = call.args.get("city")
        if isinstance(city_raw, str) and city_raw.strip():
            return None, "placeholder bbox; resolving the city instead."
        return None, "bbox has zero area; pass a real bbox [min_lon, min_lat, max_lon, max_lat] or a published city."
    if bbox_raw is not None:
        if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) != 4:
            return None, "bbox must be an array of 4 numbers [min_lon, min_lat, max_lon, max_lat]."
        try:
            return _bbox_to_polygon([float(v) for v in bbox_raw]), None
        except (TypeError, ValueError):
            return None, "bbox values must be numbers."
    return None, "missing required argument: provide aoi_geojson (GeoJSON string) or bbox."


def _resolve_dates(call: ToolCall) -> tuple[tuple[date, date] | None, str | None]:
    start_raw = call.args.get("start_date")
    end_raw = call.args.get("end_date")
    if not (isinstance(start_raw, str) and start_raw.strip()) or not (isinstance(end_raw, str) and end_raw.strip()):
        return None, "missing required arguments: start_date and end_date (ISO YYYY-MM-DD)."
    try:
        start = date.fromisoformat(start_raw)
        end = date.fromisoformat(end_raw)
    except ValueError as exc:
        return None, f"bad date (expected ISO YYYY-MM-DD): {exc}"
    if end < start:
        return None, "end_date is before start_date."
    return (start, end), None


def _resolve_run_dir(call: ToolCall) -> Path:
    """Caller-provided path, or a timestamped default under ``runs/agent``.

    Thin wrapper over ``_shared._timestamped_run_dir`` (issue #296).
    """
    return _timestamped_run_dir(call, prefix="swmm-canada")


def fetch_swmm_from_canada_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Fetch a real-pipe SWMM model from SWMMCanada for an AOI + date range."""
    aoi, error = _resolve_aoi(call)
    aoi_note: str | None = None
    city_raw = call.args.get("city")
    if error is not None and isinstance(city_raw, str) and city_raw.strip():
        # A published city named without a boundary (live test 2026-09-03,
        # S40: "downtown Regina" was a dead end). The service publishes each
        # city's coverage extent; the AOI becomes a ~1 km window at its
        # centre, said so in the result, and bbox narrows it.
        from agentic_swmm.integrations.swmmcanada_runner import (
            CanadaFetchError as _CoverageError,
            city_window,
            fetch_coverage,
            resolve_base_url,
        )

        base_raw = call.args.get("base_url")
        try:
            service_url = resolve_base_url(base_raw if isinstance(base_raw, str) and base_raw.strip() else None)
            coverage = fetch_coverage(service_url)
        except _CoverageError as exc:
            return _failure(call, str(exc), hint=_stage_hint(exc.stage))
        bbox, label, city_error = city_window(city_raw, coverage)
        if city_error is not None:
            return _failure(call, city_error)
        assert bbox is not None
        aoi = _bbox_to_polygon(bbox)
        aoi_note = (
            f"AOI = a 1 km window at the centre of the service's published coverage for {label} "
            f"(bbox {bbox}); pass bbox to choose the area."
        )
        error = None
    if error is not None:
        return _failure(call, error)
    assert aoi is not None

    error = _aoi_centre_outside_canada(aoi)
    if error is not None:
        return _failure(
            call,
            error,
            hint="use synth_swmm_from_bbox (global, synthesized) for regions outside Canada.",
        )

    dates, error = _resolve_dates(call)
    if error is not None:
        return _failure(call, error)
    assert dates is not None
    start, end = dates

    run_dir = _resolve_run_dir(call)
    base_url_raw = call.args.get("base_url")
    base_url = base_url_raw if isinstance(base_url_raw, str) and base_url_raw.strip() else None
    infiltration_raw = call.args.get("infiltration")
    infiltration = (
        infiltration_raw.strip()
        if isinstance(infiltration_raw, str) and infiltration_raw.strip()
        else None
    )

    # Lazy import — keeps the agent's import graph light. The runner is pure
    # stdlib, so this is cheap; the lazy form also matches swmm_anywhere.py and
    # lets tests patch ``swmmcanada_runner.fetch_from_aoi``.
    from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, fetch_from_aoi

    def _describe(result: Any) -> tuple[dict[str, Any], str]:
        return (
            {
                "inp_path": str(result.inp_path),
                "run_dir": str(result.run_dir),
                "zip_path": str(result.zip_path),
                "service_url": result.service_url,
                "task_id": result.task_id,
                "mode": result.mode,
                "validation": result.validation,
                "warnings": list(result.warnings),
                "aoi_note": aoi_note,
            },
            f"canada_inp={result.inp_path} (task={result.task_id}, mode={result.mode})"
            + (f"; {aoi_note}" if aoi_note else ""),
        )

    def _progress(stage: str, pct: Any) -> None:
        # Live status for the multi-minute upstream build: the poll loop
        # reports stage + progress_pct and this repaints the executor's
        # status line (no-op outside an agent run). Best-effort by design.
        text = "fetch_swmm_from_canada"
        if stage:
            text += f" — {stage}"
        if isinstance(pct, (int, float)):
            text += f" {int(pct)}%"
        update_tool_status(text)

    def _fetch(url: str | None) -> Any:
        return fetch_from_aoi(
            aoi, start, end,
            run_dir=run_dir, base_url=url, infiltration=infiltration,
            progress=_progress,
        )

    def _fetch_with_consent() -> Any:
        # Finding F-01 (live session 2026-09-02): with no URL stored
        # anywhere, the planner copied the public address out of the error
        # hint and passed it as base_url, so the opt-in that setup puts in
        # front of the user was made by the model. Ask the human the
        # wizard's own question instead; an explicit base_url is the
        # caller's decision and is never second-guessed.
        try:
            return _fetch(base_url)
        except CanadaFetchError as exc:
            if exc.stage != "config_missing" or base_url:
                raise
            enabled = _offer_inline_enable()
            if not enabled:
                raise
            return _fetch(enabled)

    return _inp_source_tool(
        call,
        fetch=_fetch_with_consent,
        describe=_describe,
        stage_hint=_stage_hint,
    )


__all__ = [
    "_aoi_centre_outside_canada",
    "_bbox_to_polygon",
    "_stage_hint",
    "fetch_swmm_from_canada_tool",
    "tool_specs",
]


def list_canada_cities_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """List the real-network cities the SWMMCanada service publishes (F-149).

    Live test 2026-09-04 (S64): "which cities can you fetch?" had no answer
    but the six examples baked into a tool description. This reads the
    service's coverage listing (one GET, nothing leaves the machine but the
    request) and returns every published city with its coverage extent.
    """
    from agentic_swmm.integrations.swmmcanada_runner import (
        CanadaFetchError as _CoverageError,
        fetch_coverage,
        resolve_base_url,
    )

    base_raw = call.args.get("base_url")
    try:
        service_url = resolve_base_url(base_raw if isinstance(base_raw, str) and base_raw.strip() else None)
        coverage = fetch_coverage(service_url)
    except _CoverageError as exc:
        return _failure(call, str(exc), hint=_stage_hint(exc.stage))
    entries = coverage.get("real_network_cities")
    cities: list[dict[str, Any]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        city: dict[str, Any] = {
            "key": str(entry.get("key") or ""),
            "label": str(entry.get("label") or entry.get("key") or ""),
        }
        extent = entry.get("coverage_bbox")
        if isinstance(extent, (list, tuple)) and len(extent) == 4:
            city["coverage_bbox"] = [float(v) for v in extent]
        for extra in ("province", "systems", "system_types"):
            if entry.get(extra) not in (None, "", []):
                city[extra] = entry[extra]
        cities.append(city)
    cities.sort(key=lambda c: c["label"].lower())
    labels = ", ".join(c["label"] for c in cities)
    summary = (
        f"{len(cities)} published real-network cities at {service_url}: {labels}. "
        "Name one (city=...) to fetch its model; pass bbox for any other Canadian area (synthesized)."
        if cities
        else f"the service at {service_url} publishes no real-network cities."
    )
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "service_url": service_url,
        "count": len(cities),
        "cities": cities,
        "summary": summary,
    }


def tool_specs() -> list[ToolSpec]:
    """This family's planner tools (issue #358 PR B self-registration).

    The registry's ``_FAMILY_SPEC_MODULES`` seam collects this next to
    the handler it names, so adding or changing a canada tool touches
    only this module.
    """
    return [
        ToolSpec(
            "list_canada_cities",
            (
                "List the Canadian cities whose REAL published municipal storm networks the "
                "SWMMCanada service can fetch, with each city's coverage extent. Read-only, one "
                "GET of the service's coverage listing. USE WHEN: the user asks which cities, "
                "areas or networks are available or covered; answer from the returned list, "
                "never from the examples in another tool's description."
            ),
            _object(
                {
                    "base_url": {
                        "type": "string",
                        "description": "Override the SWMMCanada service base URL (else $AISWMM_SWMMCANADA_URL).",
                    },
                },
                [],
            ),
            list_canada_cities_tool,
            is_read_only=True,
        ),
        ToolSpec(
            "fetch_swmm_from_canada",
            (
                "Fetch a SWMM .inp for a Canadian area via the SWMMCanada upstream "
                "HTTP service (ADR-0001). The service auto-selects REAL published "
                "municipal storm pipes where a supported city covers the AOI "
                "(35 cities at last sync, e.g. Victoria, Ottawa, Toronto, Calgary, "
                "Vancouver, Regina) and synthesizes elsewhere in Canada; the result "
                "reports which mode ran.\n"
                "A published city name is enough (city=Regina): the AOI becomes a 1 km "
                "window at the centre of the service's published coverage for that city, "
                "the result says so. bbox is for coordinates the USER gave; when the user "
                "names a city without coordinates pass city only, never a bbox of your own. "
                "Large areas exceed the 10-minute build budget.\n"
                "USE WHEN: the user wants a model for a Canadian location. Chain it: "
                "pass the returned run_dir and inp_path straight into run_swmm_inp, "
                "then audit_run, so the whole flow lands in one run folder.\n"
                "DO NOT USE WHEN: the AOI is outside Canada — use "
                "synth_swmm_from_bbox (global, synthesized) instead.\n"
                "Models are uncalibrated first-pass estimates — treat like the synth "
                "path (reference-free QA only). Requires the SWMMCanada service URL "
                "(AISWMM_SWMMCANADA_URL); returns a stage-tagged hint if unset."
            ),
            _object(
                {
                    "aoi_geojson": {
                        "type": "string",
                        "description": "GeoJSON Polygon string for the area of interest. Provide this, bbox, or city.",
                    },
                    "city": {
                        "type": "string",
                        "description": (
                            "A published real-network city (e.g. Regina, Victoria, Toronto) when the user "
                            "names a city without a boundary; resolved via the service's coverage listing "
                            "to a 1 km window at the centre of its extent."
                        ),
                    },
                    "bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                        "description": "WGS84 bounding box [min_lon, min_lat, max_lon, max_lat] the user gave; converted to a polygon. Leave it out when the user only named a city.",
                    },
                    "start_date": {"type": "string", "description": "Rainfall window start, ISO YYYY-MM-DD."},
                    "end_date": {"type": "string", "description": "Rainfall window end, ISO YYYY-MM-DD."},
                    "run_dir": {"type": "string"},
                    "base_url": {
                        "type": "string",
                        "description": "Override the SWMMCanada service base URL (else $AISWMM_SWMMCANADA_URL).",
                    },
                    "infiltration": {
                        "type": "string",
                        "enum": ["CURVE_NUMBER", "HORTON", "GREEN_AMPT"],
                        "description": (
                            "Infiltration method for the upstream build (omit for the "
                            "service default, CURVE_NUMBER). Passed through verbatim; "
                            "the SWMMCanada service validates it."
                        ),
                    },
                },
                ["start_date", "end_date"],
            ),
            fetch_swmm_from_canada_tool,
        ),
    ]
