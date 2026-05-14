from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agentic_swmm.utils.paths import repo_root, require_dir, require_file, script_path
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


def _read_manifest(run_dir: Path) -> dict[str, Any]:
    candidates = [run_dir / "manifest.json", *sorted(run_dir.glob("**/manifest.json"))]
    for path in candidates:
        if path.exists():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            return parsed if isinstance(parsed, dict) else {}
    return {}


def _resolve_recorded_path(value: str | None, run_dir: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    candidate = run_dir / path
    if candidate.exists():
        return candidate
    return repo_root() / path


def _find_inp(run_dir: Path, manifest: dict[str, Any]) -> Path | None:
    recorded = _resolve_recorded_path(manifest.get("inp"), run_dir)
    if recorded and recorded.exists():
        return recorded
    for pattern in ("00_inputs/*.inp", "04_builder/*.inp", "*.inp", "**/*.inp"):
        matches = sorted(run_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def _find_out(run_dir: Path, manifest: dict[str, Any]) -> Path | None:
    files = manifest.get("files")
    if isinstance(files, dict):
        recorded = _resolve_recorded_path(files.get("out"), run_dir)
        if recorded and recorded.exists():
            return recorded
    for pattern in ("05_runner/*.out", "01_runner/*.out", "*.out", "**/*.out"):
        matches = sorted(run_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def _infer_rain_timeseries(inp: Path) -> tuple[str, str | None]:
    options = rainfall_timeseries_options(inp)
    for option in options:
        if option.get("used_by_raingage"):
            return str(option["name"]), option.get("rain_kind")
    if options:
        return str(options[0]["name"]), options[0].get("rain_kind")
    raise FileNotFoundError(f"Unable to infer rainfall TIMESERIES from INP: {inp}")


def rainfall_timeseries_options(inp: Path) -> list[dict[str, Any]]:
    text = inp.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    raingage_series: dict[str, dict[str, str | None]] = {}
    in_raingages = False
    for raw in lines:
        stripped = raw.strip()
        upper = stripped.upper()
        if upper == "[RAINGAGES]":
            in_raingages = True
            continue
        if in_raingages and stripped.startswith("[") and stripped.endswith("]"):
            break
        if not in_raingages or not stripped or stripped.startswith(";"):
            continue
        parts = stripped.split()
        upper_parts = [p.upper() for p in parts]
        if "TIMESERIES" in upper_parts:
            idx = upper_parts.index("TIMESERIES")
            if idx + 1 < len(parts):
                name = parts[idx + 1].strip('"')
                gage = parts[0].strip('"')
                raingage_series[name] = {
                    "gage": gage,
                    "rain_kind": "cumulative_depth_mm" if "CUMULATIVE" in upper_parts else None,
                }

    options: list[dict[str, Any]] = []
    in_timeseries = False
    for raw in lines:
        stripped = raw.strip()
        upper = stripped.upper()
        if upper == "[TIMESERIES]":
            in_timeseries = True
            continue
        if in_timeseries and stripped.startswith("[") and stripped.endswith("]"):
            break
        if not in_timeseries or not stripped or stripped.startswith(";"):
            continue
        parts = stripped.split()
        if not parts:
            continue
        name = parts[0].strip('"')
        if any(option["name"] == name for option in options):
            continue
        gage_info = raingage_series.get(name, {})
        options.append(
            {
                "name": name,
                "source": "file" if len(parts) >= 3 and parts[1].upper() == "FILE" else "inline",
                "used_by_raingage": name in raingage_series,
                "gage": gage_info.get("gage"),
                "rain_kind": gage_info.get("rain_kind"),
            }
        )
    for name, gage_info in raingage_series.items():
        if not any(option["name"] == name for option in options):
            options.append(
                {
                    "name": name,
                    "source": "raingage",
                    "used_by_raingage": True,
                    "gage": gage_info.get("gage"),
                    "rain_kind": gage_info.get("rain_kind"),
                }
            )
    rainfall_options = [option for option in options if option.get("used_by_raingage")]
    return rainfall_options or options


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("plot", help="Create a rainfall-runoff plot from run artifacts.")
    parser.add_argument("--run-dir", required=True, type=Path, help="Run directory containing INP and OUT artifacts.")
    parser.add_argument("--inp", type=Path, help="Explicit INP file. Defaults to auto-discovery from run-dir.")
    parser.add_argument("--out-file", type=Path, help="Explicit SWMM .out file. Defaults to auto-discovery from run-dir.")
    parser.add_argument("--node", default="O1", help="Node/outfall to plot.")
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
        "--node",
        args.node,
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
