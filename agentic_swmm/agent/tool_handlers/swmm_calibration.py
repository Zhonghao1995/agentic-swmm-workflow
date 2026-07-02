"""Calibration tool handlers (PRD dark-MCP registration — PR 1).

Family: ``swmm-calibration``.

Six MCP-routed ToolSpec handlers that register the calibration skill's
tools as first-class typed tools so the LLM planner can select them by
name instead of going through the generic ``call_mcp_tool`` escape hatch.

Tools registered here:

* ``swmm_sensitivity_scan``   — score explicit candidate parameter sets
* ``swmm_calibrate``          — score explicit sets + optional best-promotion
* ``swmm_calibrate_search``   — bounded random/LHS/adaptive search
* ``swmm_calibrate_sceua``    — global SCE-UA with KGE (requires spotpy)
* ``swmm_calibrate_dream_zs`` — DREAM-ZS Bayesian posterior (requires spotpy)
* ``swmm_validate``           — apply accepted params to a second event

Schema source of truth: ``mcp/swmm-calibration/server.js`` Zod schemas.

Pattern: lazy-import ``_make_mcp_routed_handler`` from ``tool_registry``
at handler-build time to avoid a circular-import at module load (see
``swmm_network.py`` docstring for the full rationale).

``_failure`` comes from ``tool_handlers/_shared`` — the cross-cutting
helpers every family imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure
from agentic_swmm.agent.types import ToolCall


# ---------------------------------------------------------------------------
# Shared schema helper
# ---------------------------------------------------------------------------

def _swmm_calibrate_common_schema() -> dict[str, Any]:
    """Return the JSON-Schema properties shared by all 6 calibration tools.

    Mirrors the ``Common`` Zod object in ``mcp/swmm-calibration/server.js``.
    Callers merge additional tool-specific properties on top.
    """

    return {
        # Required base inputs
        "base_inp": {"type": "string", "description": "Path to the base SWMM .inp file to be patched for each candidate run."},
        "patch_map": {"type": "string", "description": "Path to the JSON patch-map that describes how parameter values are applied to the INP."},
        "observed": {"type": "string", "description": "Path to the observed-flow CSV (columns: timestamp, flow). Required for objective scoring."},
        "run_root": {"type": "string", "description": "Directory under which per-candidate SWMM run sub-directories are created."},
        "summary_json": {"type": "string", "description": "Path where the calibration_summary.json output is written."},
        # Optional tuning with defaults in server.js
        "swmm_node": {"type": "string", "description": "SWMM node ID whose output attribute is compared against observed data (default: 'O1')."},
        "swmm_attr": {"type": "string", "description": "SWMM output attribute to extract from the node (default: 'Total_inflow')."},
        "objective": {
            "type": "string",
            "enum": ["nse", "kge", "rmse", "bias", "peak_flow_error", "peak_timing_error"],
            "description": "Objective function to optimise. Defaults to 'nse'. Use 'kge' for swmm_calibrate_sceua and swmm_calibrate_dream_zs.",
        },
        "aggregate": {
            "type": "string",
            "enum": ["none", "daily_mean"],
            "description": "Temporal aggregation applied before scoring (default: 'none')."},
        "obs_start": {"type": "string", "description": "Optional ISO-8601 start datetime to clip the observed series."},
        "obs_end": {"type": "string", "description": "Optional ISO-8601 end datetime to clip the observed series."},
        "timestamp_col": {"type": "string", "description": "Column name for timestamps in the observed CSV (auto-detected when omitted)."},
        "flow_col": {"type": "string", "description": "Column name for flow values in the observed CSV (auto-detected when omitted)."},
        "time_format": {"type": "string", "description": "strptime format string for the timestamp column (auto-detected when omitted)."},
        "ranking_json": {"type": "string", "description": "Optional path to write a full ranking JSON (all candidates, sorted by objective)."},
        "print_ranking": {"type": "boolean", "description": "If true, print the top-N ranking table to stdout."},
        "ranking_top": {"type": "integer", "description": "Number of candidates to include in the ranking output (default: 10)."},
        "dry_run": {"type": "boolean", "description": "If true, validate inputs and resolve paths without running SWMM."},
    }


# Required properties shared by all tools (base_inp, patch_map, observed,
# run_root, summary_json). Tool-specific required props are added per tool.
_COMMON_REQUIRED = ["base_inp", "patch_map", "observed", "run_root", "summary_json"]


def _map_common_args(call: ToolCall) -> dict[str, Any]:
    """Translate the common snake_case LLM args to server.js camelCase."""

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
        "objective": "objective",
        "aggregate": "aggregate",
        "obs_start": "obsStart",
        "obs_end": "obsEnd",
        "timestamp_col": "timestampCol",
        "flow_col": "flowCol",
        "time_format": "timeFormat",
        "ranking_json": "rankingJson",
    }
    for snake, camel in _optstr.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = str(v)
    _optbool = {"print_ranking": "printRanking", "dry_run": "dryRun"}
    for snake, camel in _optbool.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = bool(v)
    v = call.args.get("ranking_top")
    if v is not None:
        args["rankingTop"] = int(v)
    return args


def _validate_common_required(call: ToolCall, session_dir: Path) -> dict[str, Any] | None:
    """Return a ``_failure`` dict if any common required arg is missing, else None."""

    for key in _COMMON_REQUIRED:
        v = call.args.get(key)
        if not isinstance(v, str) or not v.strip():
            return _failure(call, f"missing required argument: {key}")
    return None


# ---------------------------------------------------------------------------
# Per-tool args mappers
# ---------------------------------------------------------------------------

def _sensitivity_scan_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``swmm_sensitivity_scan`` args to ``swmm-calibration`` MCP schema."""

    fail = _validate_common_required(call, session_dir)
    if fail is not None:
        return fail
    parameter_sets = call.args.get("parameter_sets")
    if not isinstance(parameter_sets, str) or not parameter_sets.strip():
        return _failure(call, "missing required argument: parameter_sets")
    args = _map_common_args(call)
    args["parameterSets"] = str(parameter_sets)
    return args


def _calibrate_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``swmm_calibrate`` args to ``swmm-calibration`` MCP schema."""

    fail = _validate_common_required(call, session_dir)
    if fail is not None:
        return fail
    parameter_sets = call.args.get("parameter_sets")
    if not isinstance(parameter_sets, str) or not parameter_sets.strip():
        return _failure(call, "missing required argument: parameter_sets")
    args = _map_common_args(call)
    args["parameterSets"] = str(parameter_sets)
    if call.args.get("best_params_out"):
        args["bestParamsOut"] = str(call.args["best_params_out"])
    if call.args.get("candidate_run_dir"):
        args["candidateRunDir"] = str(call.args["candidate_run_dir"])
    return args


def _calibrate_search_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``swmm_calibrate_search`` args to ``swmm-calibration`` MCP schema."""

    fail = _validate_common_required(call, session_dir)
    if fail is not None:
        return fail
    search_space = call.args.get("search_space")
    if not isinstance(search_space, str) or not search_space.strip():
        return _failure(call, "missing required argument: search_space")
    args = _map_common_args(call)
    args["searchSpace"] = str(search_space)
    _optstr = {
        "strategy": "strategy",
    }
    for snake, camel in _optstr.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = str(v)
    _optint = {
        "iterations": "iterations",
        "rounds": "rounds",
        "seed": "seed",
    }
    for snake, camel in _optint.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = int(v)
    _optfloat = {
        "elite_fraction": "eliteFraction",
        "refine_margin": "refineMargin",
        "min_span_fraction": "minSpanFraction",
    }
    for snake, camel in _optfloat.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = float(v)
    if call.args.get("best_params_out"):
        args["bestParamsOut"] = str(call.args["best_params_out"])
    if call.args.get("candidate_run_dir"):
        args["candidateRunDir"] = str(call.args["candidate_run_dir"])
    return args


def _calibrate_sceua_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``swmm_calibrate_sceua`` args to ``swmm-calibration`` MCP schema."""

    fail = _validate_common_required(call, session_dir)
    if fail is not None:
        return fail
    search_space = call.args.get("search_space")
    if not isinstance(search_space, str) or not search_space.strip():
        return _failure(call, "missing required argument: search_space")
    args = _map_common_args(call)
    args["searchSpace"] = str(search_space)
    _optint = {
        "iterations": "iterations",
        "seed": "seed",
        "sceua_ngs": "sceuaNgs",
    }
    for snake, camel in _optint.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = int(v)
    if call.args.get("best_params_out"):
        args["bestParamsOut"] = str(call.args["best_params_out"])
    if call.args.get("convergence_csv"):
        args["convergenceCsv"] = str(call.args["convergence_csv"])
    if call.args.get("candidate_run_dir"):
        args["candidateRunDir"] = str(call.args["candidate_run_dir"])
    return args


def _calibrate_dream_zs_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``swmm_calibrate_dream_zs`` args to ``swmm-calibration`` MCP schema."""

    fail = _validate_common_required(call, session_dir)
    if fail is not None:
        return fail
    search_space = call.args.get("search_space")
    if not isinstance(search_space, str) or not search_space.strip():
        return _failure(call, "missing required argument: search_space")
    args = _map_common_args(call)
    args["searchSpace"] = str(search_space)
    _optint = {
        "iterations": "iterations",
        "seed": "seed",
        "dream_chains": "dreamChains",
        "dream_runs_after_convergence": "dreamRunsAfterConvergence",
    }
    for snake, camel in _optint.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = int(v)
    _optfloat = {
        "dream_sigma": "dreamSigma",
        "dream_rhat_threshold": "dreamRhatThreshold",
    }
    for snake, camel in _optfloat.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = float(v)
    if call.args.get("dream_output_dir"):
        args["dreamOutputDir"] = str(call.args["dream_output_dir"])
    if call.args.get("best_params_out"):
        args["bestParamsOut"] = str(call.args["best_params_out"])
    if call.args.get("candidate_run_dir"):
        args["candidateRunDir"] = str(call.args["candidate_run_dir"])
    return args


def _validate_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``swmm_validate`` args to ``swmm-calibration`` MCP schema."""

    fail = _validate_common_required(call, session_dir)
    if fail is not None:
        return fail
    best_params = call.args.get("best_params")
    if not isinstance(best_params, str) or not best_params.strip():
        return _failure(call, "missing required argument: best_params")
    args = _map_common_args(call)
    args["bestParams"] = str(best_params)
    if call.args.get("trial_name"):
        args["trialName"] = str(call.args["trial_name"])
    return args


# ---------------------------------------------------------------------------
# Handler factories (lazy-import to avoid circular import at module load)
# ---------------------------------------------------------------------------

def _build_sensitivity_scan_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler
    return _make_mcp_routed_handler(
        "swmm-calibration", "swmm_sensitivity_scan", args_mapper=_sensitivity_scan_args
    )


def _build_calibrate_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler
    return _make_mcp_routed_handler(
        "swmm-calibration", "swmm_calibrate", args_mapper=_calibrate_args
    )


def _build_calibrate_search_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler
    return _make_mcp_routed_handler(
        "swmm-calibration", "swmm_calibrate_search", args_mapper=_calibrate_search_args
    )


def _build_calibrate_sceua_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler
    return _make_mcp_routed_handler(
        "swmm-calibration", "swmm_calibrate_sceua", args_mapper=_calibrate_sceua_args
    )


def _build_calibrate_dream_zs_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler
    return _make_mcp_routed_handler(
        "swmm-calibration", "swmm_calibrate_dream_zs", args_mapper=_calibrate_dream_zs_args
    )


def _build_validate_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler
    return _make_mcp_routed_handler(
        "swmm-calibration", "swmm_validate", args_mapper=_validate_args
    )


_swmm_sensitivity_scan_tool = _build_sensitivity_scan_handler()
_swmm_calibrate_tool = _build_calibrate_handler()
_swmm_calibrate_search_tool = _build_calibrate_search_handler()
_swmm_calibrate_sceua_tool = _build_calibrate_sceua_handler()
_swmm_calibrate_dream_zs_tool = _build_calibrate_dream_zs_handler()
_swmm_validate_tool = _build_validate_handler()


__all__ = [
    "_swmm_calibrate_common_schema",
    # args mappers (exported for tests)
    "_calibrate_args",
    "_calibrate_dream_zs_args",
    "_calibrate_search_args",
    "_calibrate_sceua_args",
    "_sensitivity_scan_args",
    "_validate_args",
    # handler objects
    "_swmm_calibrate_dream_zs_tool",
    "_swmm_calibrate_search_tool",
    "_swmm_calibrate_sceua_tool",
    "_swmm_calibrate_tool",
    "_swmm_sensitivity_scan_tool",
    "_swmm_validate_tool",
]
