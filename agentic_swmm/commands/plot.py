from __future__ import annotations

import argparse
from pathlib import Path

from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.agent.swmm_runtime.inp_parsing import (
    infer_rain_timeseries as _infer_rain_timeseries,
)
from agentic_swmm.agent.swmm_runtime.inp_parsing import rainfall_timeseries_options
from agentic_swmm.agent.swmm_runtime.run_artifacts import (
    find_inp as _find_inp,
)
from agentic_swmm.agent.swmm_runtime.run_artifacts import (
    find_out as _find_out,
)
from agentic_swmm.agent.swmm_runtime.run_artifacts import (
    read_manifest as _read_manifest,
)
from agentic_swmm.utils.paths import require_dir, require_file, script_path
from agentic_swmm.utils.subprocess_runner import append_trace, python_command, run_command

DEFAULT_NODE_ATTR = "Total_inflow"

NODE_ATTRIBUTE_CHOICES = [
    "Total_inflow",
    "Lateral_inflow",
    "Flow_lost_flooding",
    "Volume_stored_ponded",
    "Depth_above_invert",
    "Hydraulic_head",
]

NODE_ATTRIBUTE_LABELS = {
    "Total_inflow": "total inflow peak or hydrograph",
    "Lateral_inflow": "lateral inflow hydrograph",
    "Flow_lost_flooding": "flooding loss hydrograph",
    "Volume_stored_ponded": "stored/ponded volume hydrograph",
    "Depth_above_invert": "node water depth above invert hydrograph",
    "Hydraulic_head": "hydraulic head hydrograph",
}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("plot", help="Create a rainfall-runoff plot from run artifacts.")
    parser.add_argument("--run-dir", required=True, type=Path, help="Run directory containing INP and OUT artifacts.")
    parser.add_argument("--inp", type=Path, help="Explicit INP file. Defaults to auto-discovery from run-dir.")
    parser.add_argument("--out-file", type=Path, help="Explicit SWMM .out file. Defaults to auto-discovery from run-dir.")
    # ``--node`` and ``--link`` are mutually exclusive: a single plot
    # call renders either a node-level series (Total_inflow / depth /
    # flooding / ...) OR a link-level series (Flow_rate / Velocity /
    # Depth). Forcing exclusivity at the CLI level surfaces the
    # ambiguity early instead of silently preferring one selector.
    entity = parser.add_mutually_exclusive_group()
    entity.add_argument("--node", help="Node/outfall to plot. Mutually exclusive with --link.")
    entity.add_argument(
        "--link",
        "--conduit",
        dest="link",
        help="Link/conduit to plot (Flow_rate hydrograph). Mutually exclusive with --node.",
    )
    parser.add_argument(
        "--node-attr",
        default=DEFAULT_NODE_ATTR,
        help="Node output attribute to plot. Common choices include Total_inflow, Depth_above_invert, Volume_stored_ponded, and Flow_lost_flooding.",
    )
    parser.add_argument("--rain-ts", help="TIMESERIES name for rainfall. Defaults to auto-discovery from INP.")
    parser.add_argument(
        "--rain-kind",
        choices=["intensity_mm_per_hr", "depth_mm_per_dt", "cumulative_depth_mm"],
        help="How to interpret rainfall values. Defaults to INP-based inference, then depth_mm_per_dt.",
    )
    parser.add_argument("--out-png", type=Path, help="Output PNG path. Defaults to run-dir/07_plots/fig_rain_runoff.png.")
    parser.add_argument("--focus-day", help="Optional focus day in YYYY-MM-DD format.")
    parser.add_argument("--window-start", help="Optional HH:MM start time when --focus-day is set.")
    parser.add_argument("--window-end", help="Optional HH:MM end time when --focus-day is set.")
    # TODO(plot-feature): re-introduce --auto-window-mode / --window-hours here
    # once plot_rain_runoff_si.py grows matching argparse + flow-peak window
    # logic. Removed because the previous version advertised and forwarded
    # them but the target script never accepted them, causing every plot_run
    # invocation to fail with argparse exit code 2.
    parser.add_argument("--pad-hours", type=float, default=2.0, help="Padding for rain auto-window mode.")
    register_example_flag(
        parser,
        example_text="aiswmm plot --run-dir runs/<case> --node O1",
    )
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    run_dir = require_dir(args.run_dir, "run directory")
    manifest = _read_manifest(run_dir)
    inp = require_file(args.inp, "INP file") if args.inp else _find_inp(run_dir, manifest)
    out_file = require_file(args.out_file, "OUT file") if args.out_file else _find_out(run_dir, manifest)
    if inp is None:
        raise FileNotFoundError(f"Unable to find an INP file in run directory: {run_dir}")
    if out_file is None:
        raise FileNotFoundError(f"Unable to find a SWMM OUT file in run directory: {run_dir}")

    out_png = args.out_png.expanduser().resolve() if args.out_png else run_dir / "07_plots" / "fig_rain_runoff.png"
    rain_ts = args.rain_ts
    inferred_rain_kind = None
    if not rain_ts:
        rain_ts, inferred_rain_kind = _infer_rain_timeseries(inp)
    rain_kind = args.rain_kind or inferred_rain_kind or "depth_mm_per_dt"

    script = script_path("skills", "swmm-plot", "scripts", "plot_rain_runoff_si.py")
    command = python_command(
        script,
        "--inp",
        str(inp),
        "--out",
        str(out_file),
        "--node-attr",
        args.node_attr,
        "--rain-ts",
        rain_ts,
        "--rain-kind",
        rain_kind,
        "--out-png",
        str(out_png),
        "--pad-hours",
        str(args.pad_hours),
    )
    # ``--node`` and ``--link`` are mutually exclusive at parse time
    # (see ``register``). Forward whichever the user picked. If neither
    # is set we fall back to the historical default node ``O1`` so the
    # pre-existing agent-driven flow (which always passes --node) is
    # not regressed.
    link_id = getattr(args, "link", None)
    node_id = getattr(args, "node", None)
    if link_id:
        command.extend(["--link", link_id])
    else:
        command.extend(["--node", node_id or "O1"])
    if args.focus_day:
        command.extend(["--focus-day", args.focus_day])
    if args.window_start:
        command.extend(["--window-start", args.window_start])
    if args.window_end:
        command.extend(["--window-end", args.window_end])
    result = run_command(command)
    append_trace(run_dir / "command_trace.json", result, stage="plot")
    print(f"plot: {out_png}")
    return result.return_code
