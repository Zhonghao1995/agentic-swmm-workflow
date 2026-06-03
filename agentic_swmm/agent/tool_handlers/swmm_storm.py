"""Design-storm generator — typed LLM-facing surface for ``aiswmm storm``.

Family: ``swmm-storm`` (no MCP server; the engine is
``agentic_swmm.agent.swmm_runtime.design_storm`` and the CLI verb
``agentic_swmm/commands/storm.py`` already owns shape selection and the
SWMM ``[TIMESERIES]`` / ``.dat`` writing).

Why this exists
---------------
aiswmm's dispatch architecture is LLM-driven over a flat tool registry (no
mode gate): the LLM picks tools by name. Design-storm generation was the
odd one out — the engine, CLI verb, and tests all existed, but the only way
the planner could reach it was ``run_allowed_command`` with hand-rolled
argv. This exposes it as a first-class typed tool so the LLM can chain
``generate_design_storm -> build_inp -> run_swmm_inp`` directly.

Mirrors ``swmm_map.py``'s thin-wrapper pattern: validate typed params,
build ``aiswmm storm`` argv, forward to ``_run_cli_tool``. The CLI verb
owns all hyetograph math, so this module stays small.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure, _run_cli_tool
from agentic_swmm.agent.types import ToolCall


def _generate_design_storm_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Generate a SWMM design-storm ``.dat`` from a named hyetograph shape.

    Required:
        ``shape``: one of uniform / triangular / front_loaded / back_loaded
            / chicago / huff / scs. Required (no silent uniform default at
            the agent surface — the intent must be explicit).
        ``out``: path to write the SWMM ``.dat`` timeseries (the artifact
            downstream ``build_inp`` consumes).

    Optional pass-throughs (1:1 with ``aiswmm storm`` flags):
        ``depth_mm``: total storm depth in mm.
        ``duration_min``: storm duration in minutes.
        ``peak_position``: 0..1 fraction for the peak (chicago / triangular).
        ``quartile``: 1..4 Huff quartile (required by ``shape=huff``).
        ``idf``: IDF spec string (e.g. ``"a=..,b=..,c=.."``); when given the
            depth is inferred and ``depth_mm`` is ignored by the CLI.
    """
    shape = call.args.get("shape")
    if not isinstance(shape, str) or not shape.strip():
        return _failure(call, "missing required argument: shape")
    out = call.args.get("out")
    if not isinstance(out, str) or not out.strip():
        return _failure(call, "missing required argument: out")

    cli_args: list[str] = ["storm", "--shape", shape.strip(), "--out", out.strip()]

    depth_raw = call.args.get("depth_mm")
    if isinstance(depth_raw, (int, float)) and not isinstance(depth_raw, bool):
        cli_args.extend(["--depth-mm", str(depth_raw)])

    duration_raw = call.args.get("duration_min")
    if isinstance(duration_raw, int) and not isinstance(duration_raw, bool) and duration_raw > 0:
        cli_args.extend(["--duration-min", str(duration_raw)])

    peak_raw = call.args.get("peak_position")
    if isinstance(peak_raw, (int, float)) and not isinstance(peak_raw, bool):
        cli_args.extend(["--peak-position", str(peak_raw)])

    quartile_raw = call.args.get("quartile")
    if isinstance(quartile_raw, int) and not isinstance(quartile_raw, bool):
        cli_args.extend(["--quartile", str(quartile_raw)])

    idf_raw = call.args.get("idf")
    if isinstance(idf_raw, str) and idf_raw.strip():
        cli_args.extend(["--idf", idf_raw.strip()])

    return _run_cli_tool(call, session_dir, cli_args)


__all__ = ["_generate_design_storm_tool"]
