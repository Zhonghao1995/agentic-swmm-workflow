"""Uncertainty tool handlers (PRD dark-MCP registration — PR 2).

Family: ``swmm-uncertainty``.

Five MCP-routed ToolSpec handlers so the LLM planner can select
uncertainty tools by name rather than via the generic ``call_mcp_tool``
escape hatch.

Tools registered here:

* ``swmm_sensitivity_oat``              — per-parameter OAT perturbation ranking
* ``swmm_sensitivity_morris``           — Morris elementary-effects screening
* ``swmm_sensitivity_sobol``            — Sobol' variance-decomposition indices
* ``swmm_rainfall_ensemble``            — forcing-uncertainty ensemble (perturbation/IDF)
* ``swmm_uncertainty_source_decomposition`` — integrate 09_audit/ artefacts into summary

Schema source of truth: ``mcp/swmm-uncertainty/server.js`` Zod schemas.

Pattern: lazy-import ``_make_mcp_routed_handler`` from ``tool_registry``
at handler-build time to avoid a circular-import at module load (same as
``swmm_calibration.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure, _object
from agentic_swmm.agent.types import ToolCall, ToolSpec


# ---------------------------------------------------------------------------
# Common schema helper — shared by OAT, Morris, Sobol
# ---------------------------------------------------------------------------

def _swmm_uncertainty_common_schema() -> dict[str, Any]:
    """Return JSON-Schema properties shared by the three sensitivity tools.

    Mirrors the ``CommonArgs`` Zod object in ``mcp/swmm-uncertainty/server.js``.
    """
    return {
        "base_inp": {"type": "string"},
        "patch_map": {"type": "string"},
        "observed": {"type": "string"},
        "run_root": {"type": "string"},
        "summary_json": {"type": "string"},
        "swmm_node": {"type": "string"},
        "swmm_attr": {"type": "string"},
        "aggregate": {"type": "string", "enum": ["none", "daily_mean"]},
        "timestamp_col": {"type": "string"},
        "flow_col": {"type": "string"},
        "time_format": {"type": "string"},
        "obs_start": {"type": "string"},
        "obs_end": {"type": "string"},
        "seed": {"type": "integer"},
    }


# Required base args shared by OAT, Morris, and Sobol.
_SENSITIVITY_REQUIRED = [
    "base_inp", "patch_map", "observed", "run_root", "summary_json"
]


def _map_common_sensitivity_args(call: ToolCall) -> dict[str, Any]:
    """Translate common snake_case sensitivity args to server.js camelCase."""
    args: dict[str, Any] = {
        "baseInp": str(call.args["base_inp"]),
        "patchMap": str(call.args["patch_map"]),
        "observed": str(call.args["observed"]),
        "runRoot": str(call.args["run_root"]),
        "summaryJson": str(call.args["summary_json"]),
    }
    _optstr = {
        "swmm_node": "swmmNode",
        "swmm_attr": "swmmAttr",
        "aggregate": "aggregate",
        "timestamp_col": "timestampCol",
        "flow_col": "flowCol",
        "time_format": "timeFormat",
        "obs_start": "obsStart",
        "obs_end": "obsEnd",
    }
    for snake, camel in _optstr.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = str(v)
    v = call.args.get("seed")
    if v is not None:
        args["seed"] = int(v)
    return args


def _validate_sensitivity_required(
    call: ToolCall, session_dir: Path
) -> dict[str, Any] | None:
    """Return a ``_failure`` dict if any required sensitivity arg is missing."""
    for key in _SENSITIVITY_REQUIRED:
        v = call.args.get(key)
        if not isinstance(v, str) or not v.strip():
            return _failure(call, f"missing required argument: {key}")
    return None


# ---------------------------------------------------------------------------
# Per-tool args mappers
# ---------------------------------------------------------------------------

def _sensitivity_oat_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``swmm_sensitivity_oat`` args to ``swmm-uncertainty`` MCP schema."""
    fail = _validate_sensitivity_required(call, session_dir)
    if fail is not None:
        return fail
    base_params = call.args.get("base_params")
    if not isinstance(base_params, str) or not base_params.strip():
        return _failure(call, "missing required argument: base_params")
    scan_spec = call.args.get("scan_spec")
    if not isinstance(scan_spec, str) or not scan_spec.strip():
        return _failure(call, "missing required argument: scan_spec")
    args = _map_common_sensitivity_args(call)
    args["baseParams"] = str(base_params)
    args["scanSpec"] = str(scan_spec)
    return args


def _sensitivity_morris_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``swmm_sensitivity_morris`` args to ``swmm-uncertainty`` MCP schema."""
    fail = _validate_sensitivity_required(call, session_dir)
    if fail is not None:
        return fail
    parameter_space = call.args.get("parameter_space")
    if not isinstance(parameter_space, str) or not parameter_space.strip():
        return _failure(call, "missing required argument: parameter_space")
    args = _map_common_sensitivity_args(call)
    args["parameterSpace"] = str(parameter_space)
    v = call.args.get("morris_r")
    if v is not None:
        args["morrisR"] = int(v)
    v = call.args.get("morris_levels")
    if v is not None:
        args["morrisLevels"] = int(v)
    return args


def _sensitivity_sobol_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``swmm_sensitivity_sobol`` args to ``swmm-uncertainty`` MCP schema."""
    fail = _validate_sensitivity_required(call, session_dir)
    if fail is not None:
        return fail
    parameter_space = call.args.get("parameter_space")
    if not isinstance(parameter_space, str) or not parameter_space.strip():
        return _failure(call, "missing required argument: parameter_space")
    args = _map_common_sensitivity_args(call)
    args["parameterSpace"] = str(parameter_space)
    v = call.args.get("sobol_n")
    if v is not None:
        args["sobolN"] = int(v)
    return args


def _rainfall_ensemble_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``swmm_rainfall_ensemble`` args to ``swmm-uncertainty`` MCP schema."""
    method = call.args.get("method")
    if not isinstance(method, str) or method not in ("perturbation", "idf"):
        return _failure(call, "missing required argument: method (must be 'perturbation' or 'idf')")
    config = call.args.get("config")
    if not isinstance(config, str) or not config.strip():
        return _failure(call, "missing required argument: config")
    run_root = call.args.get("run_root")
    if not isinstance(run_root, str) or not run_root.strip():
        return _failure(call, "missing required argument: run_root")
    args: dict[str, Any] = {
        "method": str(method),
        "config": str(config),
        "runRoot": str(run_root),
    }
    if call.args.get("base_inp"):
        args["baseInp"] = str(call.args["base_inp"])
    _optstr = {
        "series_name": "seriesName",
        "swmm_node": "swmmNode",
    }
    for snake, camel in _optstr.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = str(v)
    v = call.args.get("seed")
    if v is not None:
        args["seed"] = int(v)
    v = call.args.get("dry_run")
    if v is not None:
        args["dryRun"] = bool(v)
    return args


def _source_decomposition_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``swmm_uncertainty_source_decomposition`` args to ``swmm-uncertainty`` MCP schema."""
    run_dir = call.args.get("run_dir")
    if not isinstance(run_dir, str) or not run_dir.strip():
        return _failure(call, "missing required argument: run_dir")
    return {"runDir": str(run_dir)}


# ---------------------------------------------------------------------------
# Handler factories (lazy-import to avoid circular import at module load)
# ---------------------------------------------------------------------------

def _build_sensitivity_oat_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler
    return _make_mcp_routed_handler(
        "swmm-uncertainty", "swmm_sensitivity_oat", args_mapper=_sensitivity_oat_args
    )


def _build_sensitivity_morris_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler
    return _make_mcp_routed_handler(
        "swmm-uncertainty", "swmm_sensitivity_morris", args_mapper=_sensitivity_morris_args
    )


def _build_sensitivity_sobol_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler
    return _make_mcp_routed_handler(
        "swmm-uncertainty", "swmm_sensitivity_sobol", args_mapper=_sensitivity_sobol_args
    )


def _build_rainfall_ensemble_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler
    return _make_mcp_routed_handler(
        "swmm-uncertainty", "swmm_rainfall_ensemble", args_mapper=_rainfall_ensemble_args
    )


def _build_source_decomposition_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler
    return _make_mcp_routed_handler(
        "swmm-uncertainty",
        "swmm_uncertainty_source_decomposition",
        args_mapper=_source_decomposition_args,
    )


_swmm_sensitivity_oat_tool = _build_sensitivity_oat_handler()
_swmm_sensitivity_morris_tool = _build_sensitivity_morris_handler()
_swmm_sensitivity_sobol_tool = _build_sensitivity_sobol_handler()
_swmm_rainfall_ensemble_tool = _build_rainfall_ensemble_handler()
_swmm_uncertainty_source_decomposition_tool = _build_source_decomposition_handler()


__all__ = [
    "_swmm_uncertainty_common_schema",
    "_SENSITIVITY_REQUIRED",
    # args mappers (exported for tests)
    "_sensitivity_oat_args",
    "_sensitivity_morris_args",
    "_sensitivity_sobol_args",
    "_rainfall_ensemble_args",
    "_source_decomposition_args",
    # handler objects
    "_swmm_sensitivity_oat_tool",
    "_swmm_sensitivity_morris_tool",
    "_swmm_sensitivity_sobol_tool",
    "_swmm_rainfall_ensemble_tool",
    "_swmm_uncertainty_source_decomposition_tool",
    "tool_specs",
]


def tool_specs() -> list[ToolSpec]:
    """The five swmm-uncertainty planner tools (issue #358 self-registration).

    Dark-MCP registration (PR 2, issue #246); all is_read_only=False —
    each writes artefacts.
    """
    return [
        ToolSpec(
            "swmm_sensitivity_oat",
            "OAT sensitivity: perturb each parameter around a baseline and rank by RMSE+peak-error spread.",
            _object(
                {
                    **_swmm_uncertainty_common_schema(),
                    "base_params": {"type": "string", "description": "JSON object of baseline parameter values."},
                    "scan_spec": {"type": "string", "description": "JSON object: parameter -> list of trial values."},
                },
                _SENSITIVITY_REQUIRED + ["base_params", "scan_spec"],
            ),
            _swmm_sensitivity_oat_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_sensitivity_morris",
            "Morris elementary-effects screening via SALib; budget = r*(k+1) SWMM runs.",
            _object(
                {
                    **_swmm_uncertainty_common_schema(),
                    "parameter_space": {"type": "string", "description": "JSON: parameter -> {min, max} bounds."},
                    "morris_r": {"type": "integer", "description": "Trajectory count; budget = r*(k+1)."},
                    "morris_levels": {"type": "integer"},
                },
                _SENSITIVITY_REQUIRED + ["parameter_space"],
            ),
            _swmm_sensitivity_morris_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_sensitivity_sobol",
            "Sobol' variance-decomposition (S_i + S_T_i) via SALib Saltelli sampling; budget = N*(2k+2) runs.",
            _object(
                {
                    **_swmm_uncertainty_common_schema(),
                    "parameter_space": {"type": "string", "description": "JSON: parameter -> {min, max} bounds."},
                    "sobol_n": {"type": "integer", "description": "Saltelli base sample size; budget = N*(2k+2)."},
                },
                _SENSITIVITY_REQUIRED + ["parameter_space"],
            ),
            _swmm_sensitivity_sobol_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_rainfall_ensemble",
            "Generate a rainfall ensemble (perturbation of observed series or IDF design storms); optionally run swmm5 per realisation.",
            _object(
                {
                    "method": {"type": "string", "enum": ["perturbation", "idf"], "description": "Ensemble generation method."},
                    "config": {"type": "string", "description": "Path to JSON config (see skills/swmm-uncertainty/examples/)."},
                    "run_root": {"type": "string", "description": "Output root; summary at <run_root>/09_audit/rainfall_ensemble_summary.json."},
                    "base_inp": {"type": "string", "description": "If provided, each realisation is patched into this INP and run through swmm5."},
                    "series_name": {"type": "string"},
                    "swmm_node": {"type": "string"},
                    "seed": {"type": "integer"},
                    "dry_run": {"type": "boolean", "description": "Generate realisations but skip swmm5."},
                },
                ["method", "config", "run_root"],
            ),
            _swmm_rainfall_ensemble_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_uncertainty_source_decomposition",
            "Integrate existing 09_audit/ artefacts (Sobol'/Morris/DREAM-ZS/SCE-UA/ensemble) into uncertainty_source_summary.md.",
            _object(
                {
                    "run_dir": {"type": "string", "description": "Run directory containing 09_audit/."},
                },
                ["run_dir"],
            ),
            _swmm_uncertainty_source_decomposition_tool,
            is_read_only=False,
        ),
    ]
