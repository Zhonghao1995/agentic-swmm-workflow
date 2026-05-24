"""Rainfall / climate formatting handler (PRD #128 Phase 2 Group B).

Family: ``swmm-climate``.

Single-handler family extracted from ``tool_registry.py`` as part of
Phase 2 Group B of the registry split (Phase 1 — cross-cutting
helpers — landed in PR #209, see ``_shared.py``):

* :func:`_format_rainfall_tool` — converts a rainfall CSV into a SWMM
  TIMESERIES text + metadata JSON pair via the
  ``swmm-climate.format_rainfall`` MCP tool.

The handler is MCP-routed. See ``swmm_network.py`` for the rationale
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

    input_csv = _required_repo_file(call, "input_csv", suffix=".csv")
    if isinstance(input_csv, dict):
        return input_csv
    out_json = _repo_output_path(str(call.args["out_json"]))
    out_timeseries = _repo_output_path(str(call.args["out_timeseries"]))
    if out_json is None or out_json.suffix.lower() != ".json":
        return _failure(call, "out_json must be a repository-relative .json path")
    if out_timeseries is None or out_timeseries.suffix.lower() not in {".txt", ".dat"}:
        return _failure(call, "out_timeseries must be a repository-relative .txt or .dat path")
    args: dict[str, Any] = {
        "inputCsvPath": str(input_csv),
        "outputJsonPath": str(out_json),
        "outputTimeseriesPath": str(out_timeseries),
    }
    snake_to_camel = {
        "series_name": "seriesName",
        "timestamp_column": "timestampColumn",
        "value_column": "valueColumn",
        "value_units": "valueUnits",
        "unit_policy": "unitPolicy",
        "timestamp_policy": "timestampPolicy",
    }
    for snake, camel in snake_to_camel.items():
        if call.args.get(snake):
            args[camel] = str(call.args[snake])
    return args


def _build_handler() -> Any:
    from agentic_swmm.agent.tool_registry import _make_mcp_routed_handler

    return _make_mcp_routed_handler(
        "swmm-climate", "format_rainfall", args_mapper=_format_rainfall_args
    )


_format_rainfall_tool = _build_handler()


__all__ = [
    "_format_rainfall_args",
    "_format_rainfall_tool",
]
