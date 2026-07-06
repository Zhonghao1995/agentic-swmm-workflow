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
import time
from datetime import date
from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _inp_source_tool,
    _safe_name,
)
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root


def _stage_hint(stage: str) -> str:
    """Return an actionable hint for a ``CanadaFetchError`` stage tag."""
    if stage == "config_missing":
        return (
            "set the SWMMCanada service URL via the AISWMM_SWMMCANADA_URL "
            "environment variable (a local container at http://localhost:8000 "
            "or a hosted backend), or pass base_url."
        )
    if stage == "task_failed":
        return (
            "the service rejected this AOI — SWMMCanada produces real municipal "
            "pipe networks only for supported Canadian cities (Victoria, Ottawa, "
            "Calgary, Surrey, London, Kitchener-Waterloo, Kelowna). For other "
            "regions use synth_swmm_from_bbox instead."
        )
    if stage == "timeout":
        return (
            "the model did not finish in time; the live pipeline fetches external "
            "open data and can be slow. Retry, or raise the timeout."
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


def _resolve_aoi(call: ToolCall) -> tuple[str | None, str | None]:
    """Return ``(aoi_geojson, error)``. Accepts an explicit GeoJSON string or a bbox."""
    aoi_raw = call.args.get("aoi_geojson")
    if isinstance(aoi_raw, str) and aoi_raw.strip():
        return aoi_raw, None
    bbox_raw = call.args.get("bbox")
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
    """Caller-provided absolute path, or a timestamped default under ``runs/agent``.

    Mirrors the swmm-anywhere handler so a re-fetch never silently overwrites
    a previous run's directory.
    """
    raw = call.args.get("run_dir")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    base = f"swmm-canada-{int(time.time())}"
    root = repo_root() / "runs" / "agent"
    candidate = root / base
    bump = 1
    while candidate.exists():
        bump += 1
        candidate = root / f"{base}-{bump}"
    return candidate


def fetch_swmm_from_canada_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Fetch a real-pipe SWMM model from SWMMCanada for an AOI + date range."""
    aoi, error = _resolve_aoi(call)
    if error is not None:
        return _failure(call, error)
    assert aoi is not None

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
    from agentic_swmm.integrations.swmmcanada_runner import fetch_from_aoi

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
            },
            f"canada_inp={result.inp_path} (task={result.task_id}, mode={result.mode})",
        )

    return _inp_source_tool(
        call,
        fetch=lambda: fetch_from_aoi(
            aoi, start, end, run_dir=run_dir, base_url=base_url, infiltration=infiltration
        ),
        describe=_describe,
        stage_hint=_stage_hint,
    )


__all__ = [
    "_bbox_to_polygon",
    "_stage_hint",
    "fetch_swmm_from_canada_tool",
]
