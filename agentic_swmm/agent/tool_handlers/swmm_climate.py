"""Rainfall / climate formatting handler (PRD #128 Phase 2 Group B).

Family: ``swmm-climate``.

Single-handler family extracted from ``tool_registry.py`` as part of
Phase 2 Group B of the registry split (Phase 1 — cross-cutting
helpers — landed in PR #209, see ``_shared.py``):

* :func:`_format_rainfall_tool` — converts a rainfall CSV into a SWMM
  TIMESERIES text + metadata JSON pair via the
  ``swmm-climate.format_rainfall`` MCP tool.
* :func:`_build_raingage_section_tool` — builds the SWMM [RAINGAGES]
  section snippet that pairs with a formatted timeseries (issue #246 C1).

The handlers are MCP-routed. See ``swmm_network.py`` for the rationale
behind the lazy-build / lazy-import pattern (avoids the
``tool_registry`` import cycle).

``_failure`` comes from ``tool_handlers/_shared`` — the cross-cutting
helpers every family imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure
from agentic_swmm.agent.types import ToolCall


def _format_rainfall_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``format_rainfall`` args to ``swmm-climate.format_rainfall`` MCP."""

    # Lazy import — see ``swmm_network`` module docstring.
    from agentic_swmm.agent.tool_registry import (
        _repo_output_path,
        _required_repo_file,
    )

    # At least one of input_csv, input_glob_patterns, or input_dat_paths is
    # required at the MCP layer; we validate the single-CSV case explicitly
    # (the most common agent path) and pass all other inputs through.
    input_csv = call.args.get("input_csv")
    input_glob_patterns = call.args.get("input_glob_patterns")
    input_dat_paths = call.args.get("input_dat_paths")
    if not input_csv and not input_glob_patterns and not input_dat_paths:
        return _failure(call, "format_rainfall requires input_csv, input_glob_patterns, or input_dat_paths")
    out_json = _repo_output_path(str(call.args["out_json"]))
    out_timeseries = _repo_output_path(str(call.args["out_timeseries"]))
    if out_json is None or out_json.suffix.lower() != ".json":
        return _failure(call, "out_json must be a repository-relative .json path")
    if out_timeseries is None or out_timeseries.suffix.lower() not in {".txt", ".dat"}:
        return _failure(call, "out_timeseries must be a repository-relative .txt or .dat path")
    args: dict[str, Any] = {
        "outputJsonPath": str(out_json),
        "outputTimeseriesPath": str(out_timeseries),
    }
    if input_csv:
        args["inputCsvPath"] = str(input_csv)
    if input_glob_patterns:
        args["inputGlobPatterns"] = list(input_glob_patterns)
    if input_dat_paths:
        args["inputDatPaths"] = list(input_dat_paths)
    snake_to_camel = {
        "additional_input_csv_paths": "additionalInputCsvPaths",
        "dat_value_units": "datValueUnits",
        "series_name": "seriesName",
        "series_name_template": "seriesNameTemplate",
        "timestamp_column": "timestampColumn",
        "value_column": "valueColumn",
        "station_column": "stationColumn",
        "default_station_id": "defaultStationId",
        "timestamp_format": "timestampFormat",
        "window_start": "windowStart",
        "window_end": "windowEnd",
        "value_units": "valueUnits",
        "unit_policy": "unitPolicy",
        "timestamp_policy": "timestampPolicy",
    }
    for snake, camel in snake_to_camel.items():
        val = call.args.get(snake)
        if val:
            args[camel] = val if isinstance(val, list) else str(val)
    return args


def _build_raingage_section_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``build_raingage_section`` args to ``swmm-climate.build_raingage_section`` MCP."""

    from agentic_swmm.agent.tool_registry import _repo_output_path

    out_text = call.args.get("out_text_path")
    out_json = call.args.get("out_json_path")
    if not out_text:
        return _failure(call, "missing required argument: out_text_path")
    if not out_json:
        return _failure(call, "missing required argument: out_json_path")
    out_text_path = _repo_output_path(str(out_text))
    out_json_path = _repo_output_path(str(out_json))
    if out_text_path is None:
        return _failure(call, "out_text_path must be a repository-relative path")
    if out_json_path is None or out_json_path.suffix.lower() != ".json":
        return _failure(call, "out_json_path must be a repository-relative .json path")
    args: dict[str, Any] = {
        "outTextPath": str(out_text_path),
        "outJsonPath": str(out_json_path),
    }
    snake_to_camel = {
        "gage_id": "gageId",
        "series_name": "seriesName",
        "station_id": "stationId",
        "rainfall_json_path": "rainfallJsonPath",
        "rain_format": "rainFormat",
    }
    for snake, camel in snake_to_camel.items():
        if call.args.get(snake):
            args[camel] = str(call.args[snake])
    if call.args.get("interval_min") is not None:
        args["intervalMin"] = int(call.args["interval_min"])
    if call.args.get("scf") is not None:
        args["scf"] = float(call.args["scf"])
    return args


def _generate_design_storm_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``generate_design_storm`` args to ``swmm-climate.generate_design_storm`` MCP."""

    from agentic_swmm.agent.tool_registry import _repo_output_path

    method = call.args.get("method")
    if not isinstance(method, str) or method not in ("chicago", "alternating_block"):
        return _failure(call, "method must be 'chicago' or 'alternating_block'")
    duration = call.args.get("duration_min")
    if duration is None:
        return _failure(call, "missing required argument: duration_min")
    out_json = call.args.get("out_json")
    if not out_json:
        return _failure(call, "missing required argument: out_json")
    out_ts = call.args.get("out_timeseries")
    if not out_ts:
        return _failure(call, "missing required argument: out_timeseries")

    out_json_path = _repo_output_path(str(out_json))
    out_ts_path = _repo_output_path(str(out_ts))
    if out_json_path is None or out_json_path.suffix.lower() != ".json":
        return _failure(call, "out_json must be a repository-relative .json path")
    if out_ts_path is None or out_ts_path.suffix.lower() not in {".txt", ".dat"}:
        return _failure(call, "out_timeseries must be a repository-relative .txt or .dat path")

    args: dict[str, Any] = {
        "method": method,
        "duration": float(duration),
        "outJson": str(out_json_path),
        "outTimeseries": str(out_ts_path),
    }

    # Optional scalar fields — snake_case → camelCase
    _optfloat = {
        "return_period": "returnPeriod",
        "dt": "dt",
        "r": "r",
        "a1": "a1",
        "b": "b",
        "n": "n",
        "a_coeff": "aCoeff",
        "c_coeff": "cCoeff",
        "c_exp": "cExp",
    }
    for snake, camel in _optfloat.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = float(v)

    _optstr = {
        "form": "form",
        "idf_csv": "idfCsv",
        "idf_json": "idfJson",
        "series_name": "seriesName",
    }
    for snake, camel in _optstr.items():
        v = call.args.get(snake)
        if v is not None:
            args[camel] = str(v)

    return args


def _build_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler

    return _make_mcp_routed_handler(
        "swmm-climate", "format_rainfall", args_mapper=_format_rainfall_args
    )


def _build_raingage_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler

    return _make_mcp_routed_handler(
        "swmm-climate", "build_raingage_section", args_mapper=_build_raingage_section_args
    )


def _build_design_storm_handler() -> Any:
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler

    return _make_mcp_routed_handler(
        "swmm-climate", "generate_design_storm", args_mapper=_generate_design_storm_args
    )


_format_rainfall_tool = _build_handler()
_build_raingage_section_tool = _build_raingage_handler()
_generate_design_storm_tool = _build_design_storm_handler()


__all__ = [
    "_build_raingage_section_args",
    "_build_raingage_section_tool",
    "_format_rainfall_args",
    "_format_rainfall_tool",
    "_generate_design_storm_args",
    "_generate_design_storm_tool",
]
