"""SWMManywhere typed-tool handler (LLM-driven dispatch refactor).

Family: ``swmm-anywhere``.

Surfaces the ``synth_swmm_from_bbox`` tool — a typed wrapper around
``agentic_swmm.integrations.swmmanywhere_runner.run_synth_from_bbox``
that the LLM can pick directly from the tool registry, with no
``select_workflow_mode`` gate in front of it.

The handler is **in-process** (not MCP-routed): SWMManywhere is a
Python library, so we call it directly instead of routing through an
MCP server. That keeps the dispatch chain shallow:

    LLM → tool_registry → _synth_swmm_from_bbox_tool →
                          run_synth_from_bbox(...) → SWMManywhere

Typed-param validation mirrors the OpenAI / Anthropic function-calling
shape: every required argument is checked here so a malformed call
fails with a fail-soft ``_failure(...)`` payload rather than raising
into the planner loop.

The stage-aware error hint mirrors
``skills/swmm-anywhere/scripts/synth_from_bbox.py``'s CLI mapping so
the LLM sees the same actionable guidance whether the user invokes
the skill from the agent or from the shell.

``_failure`` comes from ``tool_handlers/_shared`` — the cross-cutting
helpers every family imports.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure, _safe_name
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root


def _stage_hint(stage: str) -> str:
    """Return an actionable hint for a SynthRunError stage tag.

    Mirrors the CLI mapping in
    ``skills/swmm-anywhere/scripts/synth_from_bbox.py`` so a user who
    drives the skill via the agent sees the same guidance as one who
    drove it from the shell.
    """
    if stage == "extra_missing":
        return (
            "this skill requires the optional [anywhere] extra, which wraps "
            "SWMManywhere by Imperial College London (BSD-3-Clause, "
            "https://github.com/ImperialCollegeLondon/SWMManywhere). "
            "Install with: pip install aiswmm[anywhere]"
        )
    if stage == "rain_file_missing":
        return (
            "--rain-file must point at an existing SWMM-format DAT file (an "
            "absolute path is safest). See the SWMM 5.2 manual [RAINGAGES] "
            "FILE format for the expected layout."
        )
    return (
        "re-run with refresh_raw=true if the failure was a stale OSM / DEM "
        "snapshot, or pass a smaller bbox if the failure was OOM."
    )


def _validate_bbox(raw: Any) -> tuple[list[float] | None, str | None]:
    """Validate a bbox argument and return ``(bbox, error)``.

    Returns ``(bbox, None)`` on success, ``(None, error)`` on failure.
    A bbox is exactly four numbers ``[min_lon, min_lat, max_lon, max_lat]``
    in WGS84; the runner re-validates downstream but the typed layer
    is the LLM-facing surface so we fail fast and clearly here.
    """
    if raw is None:
        return None, "missing required argument: bbox"
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None, "bbox must be a 4-element array [min_lon, min_lat, max_lon, max_lat]"
    coerced: list[float] = []
    for value in raw:
        if isinstance(value, bool):
            return None, "bbox entries must be numbers, not booleans"
        if not isinstance(value, (int, float)):
            return None, "bbox entries must be numbers"
        coerced.append(float(value))
    min_lon, min_lat, max_lon, max_lat = coerced
    if not (min_lon < max_lon and min_lat < max_lat):
        return None, "bbox must satisfy min_lon<max_lon and min_lat<max_lat"
    return coerced, None


def _resolve_run_dir(call: ToolCall) -> Path:
    """Pick the run directory: caller-provided absolute path or a
    repo-relative default under ``runs/agent/<safe>-<unix-ts>``.

    Mirrors the ``run_swmm_inp`` handler's timestamped default so a
    re-run of the same project name never lands in (and silently
    overwrites) a previous run's directory (issue #246/#234). If the
    timestamped name already exists on disk, a ``-N`` suffix bumps
    until a fresh directory is found. An EXPLICIT ``run_dir`` is
    passed through untouched — same-dir reuse (e.g. the synth
    ``00_raw/`` snapshot workflow) stays a deliberate caller choice.
    """
    raw = call.args.get("run_dir")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    project = str(call.args.get("project_name") or "swmm-anywhere")
    base = f"{_safe_name(project)}-{int(time.time())}"
    root = repo_root() / "runs" / "agent"
    candidate = root / base
    bump = 1
    while candidate.exists():
        bump += 1
        candidate = root / f"{base}-{bump}"
    return candidate


def _synth_swmm_from_bbox_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Run SWMManywhere on a bbox via the in-process integration wrapper.

    Validates typed params before invoking the heavy import path so a
    malformed LLM call fails fast with a structured ``_failure`` payload
    instead of crashing inside SWMManywhere.
    """
    bbox, error = _validate_bbox(call.args.get("bbox"))
    if error is not None:
        return _failure(call, error)
    assert bbox is not None  # narrow for type checkers

    run_dir = _resolve_run_dir(call)
    project_name = str(call.args.get("project_name") or "swmm_anywhere")
    refresh_raw = bool(call.args.get("refresh_raw"))
    upstream_defaults = bool(call.args.get("upstream_defaults"))
    rain_file_raw = call.args.get("rain_file")
    rain_file = (
        Path(str(rain_file_raw)).expanduser()
        if isinstance(rain_file_raw, str) and rain_file_raw.strip()
        else None
    )

    # Lazy import — keeps the agent's import graph light when the
    # [anywhere] extra is not installed. The integration wrapper itself
    # checks for the extra and raises ``SynthRunError(stage='extra_missing')``,
    # which we map to a fail-soft hint below.
    from agentic_swmm.integrations.swmmanywhere_runner import (
        SynthRunError,
        run_synth_from_bbox,
    )

    try:
        result = run_synth_from_bbox(
            bbox=list(bbox),
            run_dir=run_dir,
            project_name=project_name,
            refresh_raw=refresh_raw,
            use_upstream_defaults=upstream_defaults,
            rain_file=rain_file,
        )
    except SynthRunError as exc:
        payload = _failure(
            call,
            f"swmm-anywhere stage '{exc.stage}' failed: {exc.original_exc!r}",
        )
        payload["stage"] = exc.stage
        payload["hint"] = _stage_hint(exc.stage)
        return payload

    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": {
            "inp_path": str(result.inp_path),
            "run_dir": str(result.run_dir),
            "raw_manifest_path": str(result.raw_manifest_path),
            "stage_durations_s": dict(result.stage_durations),
            "warnings": list(result.warnings),
            "provenance": dict(result.provenance),
        },
        "summary": f"synth_inp={result.inp_path}",
    }


__all__ = [
    "_synth_swmm_from_bbox_tool",
    "_stage_hint",
    "_validate_bbox",
    "_resolve_run_dir",
]
