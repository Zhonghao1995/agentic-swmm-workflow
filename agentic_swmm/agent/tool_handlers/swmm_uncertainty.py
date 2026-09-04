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


def propagate_parameter_ranges_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """In-process handler for ``propagate_parameter_ranges`` (user decision 2026-09-02, F-55).

    Reference-free propagation: apply each named parameter globally over a
    user-given range, run SWMM once per sample through the audited runner,
    and report the spread of the peak at the report node. Pure in-process
    orchestration like ``run_climate_scenarios``: no MCP routing and no new
    EXPECTED_BINDINGS row.
    """
    from agentic_swmm.agent.swmm_runtime import parameter_sweep
    from agentic_swmm.agent.tool_handlers._shared import _timestamped_run_dir
    from agentic_swmm.agent.tool_registry import _resolve_existing_inp

    inp_raw = call.args.get("inp_path")
    if not isinstance(inp_raw, str) or not inp_raw.strip():
        return _failure(call, "propagate_parameter_ranges requires inp_path")
    inp = _resolve_existing_inp(inp_raw)
    if inp is None:
        # Live finding F-105 (2026-09-03, S46): a guessed 06_runner/model.inp
        # got a bare "not found"; name the INP files that do exist instead.
        from agentic_swmm.agent.tool_handlers._shared import _missing_file_failure, _repo_path

        candidate = _repo_path(inp_raw)
        if candidate is None:
            return _failure(call, f"INP not found (in-repo paths only): {inp_raw}")
        failure = _missing_file_failure(call, candidate, ".inp")
        failure["summary"] = f"INP not found (in-repo paths only): {inp_raw}"
        return failure
    try:
        ranges = parameter_sweep.parse_ranges(call.args.get("ranges"))
    except ValueError as exc:
        return _failure(
            call,
            f"bad ranges: {exc}",
            hint='ranges is a mapping such as {"n_imperv": [0.010, 0.020], "pct_imperv": [60, 80]} '
            "(aliases: manning_n, imperviousness, conduit_roughness, n_perv, s_imperv, s_perv, width, slope).",
        )
    explicit_run_dir = call.args.get("run_dir")
    if isinstance(explicit_run_dir, str) and explicit_run_dir.strip():
        run_dir = Path(explicit_run_dir)
    else:
        run_dir = _timestamped_run_dir(call, prefix="sweep")
    node_raw = call.args.get("node")
    node = node_raw.strip() if isinstance(node_raw, str) and node_raw.strip() else None
    n_samples_raw = call.args.get("n_samples")
    try:
        n_samples = int(n_samples_raw) if n_samples_raw not in (None, "") else None
    except (TypeError, ValueError):
        return _failure(call, "n_samples must be an integer")
    try:
        mode_raw = call.args.get("mode")
        mode = str(mode_raw).strip() if isinstance(mode_raw, str) and mode_raw.strip() else "joint"
        if mode not in ("joint", "one_at_a_time"):
            return _failure(call, "mode must be 'joint' or 'one_at_a_time'")
        result = parameter_sweep.run_parameter_sweep(
            base_inp=inp, run_dir=run_dir, ranges=ranges, node=node, n_samples=n_samples, mode=mode
        )
    except Exception as exc:  # noqa: BLE001 - the planner needs the message, not a traceback
        return _failure(call, f"parameter sweep failed: {exc}")
    stats = result.stats
    spread = (
        f"peak {stats['peak_min']:g}..{stats['peak_max']:g} {result.flow_units or ''}".strip()
        + (f" ({stats['spread_percent_of_baseline']}% of baseline {result.baseline_peak:g})" if "spread_percent_of_baseline" in stats and result.baseline_peak else "")
        if "peak_min" in stats
        else "no successful sample"
    )
    return {
        "tool": call.name,
        "args": call.args,
        "ok": result.ok,
        "run_dir": result.run_dir,
        "node": result.node,
        "baseline_peak": result.baseline_peak,
        "flow_units": result.flow_units,
        "ranges": {k: list(v) for k, v in ranges.items()},
        "mode": mode,
        "ranking": list(result.stats.get("ranking") or []),
        "stats": stats,
        "samples": [
            {"name": s.name, "values": s.values, "run_ok": s.run_ok, "peak": s.peak}
            for s in result.samples
        ],
        "summary_json": result.summary_json,
        "summary_md": result.summary_md,
        "evidence_boundary": "Prior sensitivity of the peak to globally applied parameter ranges, not calibrated uncertainty.",
        "summary": (
            f"{stats.get('samples_ok', 0)}/{stats.get('samples_total', 0)} samples ran at {result.node}: {spread}"
            + (f"; dominant parameter {stats['dominant_parameter']}" if "dominant_parameter" in stats else "")
            + f"; summary at {result.summary_md}"
        ),
    }


__all__ = [
    "propagate_parameter_ranges_tool",
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
            "OAT sensitivity AGAINST OBSERVED FLOW: perturb each parameter around a baseline and rank by "
            "RMSE+peak-error spread. Needs an observed series and a patch map. Without observed data, "
            "use propagate_parameter_ranges with mode=one_at_a_time for a reference-free ranking.",
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
            "Rainfall-forcing ensemble from a PREPARED rainfall series file and JSON config (method perturbation or idf). Not for a plain scaled event: for 'scale the observed event by 0.8, 1.0 and 1.2' on a model whose rain is inline in the INP, call run_climate_scenarios with those factors. " "Generate a rainfall ensemble (perturbation of observed series or IDF design storms); optionally run swmm5 per realisation.",
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
            "propagate_parameter_ranges",
            (
                "Reference-free uncertainty: apply each named parameter GLOBALLY over a range "
                "(the same value on every subcatchment or conduit), run SWMM once per sample, "
                "and report the spread of the peak at the report node.\n"
                "mode=one_at_a_time varies each parameter alone (the others at baseline) in ONE call and "
                "returns a per-parameter spread and a ranking: the reference-free answer to 'which "
                "parameters matter most' or 'a one-at-a-time scan'. Do not repeat this tool per parameter. "
                "With observed flow available, the swmm_sensitivity_* tools rank against the data instead.\n"
                "USE WHEN: the user asks how uncertain the peak is, or to vary Manning's n, "
                "imperviousness, depression storage or conduit roughness, and has NO observed "
                "flow (the sensitivity tools need an observed series).\n"
                'ranges example: {"n_imperv": [0.010, 0.020], "pct_imperv": [60, 80]}; aliases '
                "manning_n, imperviousness, conduit_roughness. 25 samples by default (5x5 grid "
                "for two parameters, Latin hypercube beyond 36). Results: 09_audit/parameter_sweep.json and .md."
            ),
            _object(
                {
                    "mode": {
                        "type": "string",
                        "enum": ["joint", "one_at_a_time"],
                        "description": "joint (default): sample all ranges together and report the spread; one_at_a_time: vary each parameter alone and rank them (n_samples is then the levels PER parameter, default 5, capped at 9; each level is one SWMM run).",
                    },
                    "inp_path": {"type": "string", "description": "Existing SWMM .inp (in-repo path)."},
                    "ranges": {"type": "object", "description": "Parameter name -> [low, high]."},
                    "node": {"type": "string", "description": "Report node; default = the dominant outfall."},
                    "n_samples": {"type": "integer", "description": "Override the sample count."},
                    "run_dir": {"type": "string", "description": "Run directory; default = the current run."},
                },
                ["inp_path", "ranges"],
            ),
            propagate_parameter_ranges_tool,
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
