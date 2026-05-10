from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agentic_swmm.utils.paths import repo_root, require_dir, require_file, script_path
from agentic_swmm.utils.subprocess_runner import append_trace, python_command, run_command


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
    text = inp.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
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
                rain_kind = "cumulative_depth_mm" if "CUMULATIVE" in upper_parts else None
                return parts[idx + 1].strip('"'), rain_kind

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
        return stripped.split()[0], None
    raise FileNotFoundError(f"Unable to infer rainfall TIMESERIES from INP: {inp}")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("plot", help="Create a rainfall-runoff plot from run artifacts.")
    parser.add_argument("--run-dir", required=True, type=Path, help="Run directory containing INP and OUT artifacts.")
    parser.add_argument("--inp", type=Path, help="Explicit INP file. Defaults to auto-discovery from run-dir.")
    parser.add_argument("--out-file", type=Path, help="Explicit SWMM .out file. Defaults to auto-discovery from run-dir.")
    parser.add_argument("--node", default="O1", help="Node/outfall to plot.")
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
    parser.add_argument("--auto-window-mode", choices=["flow-peak", "rain", "full"], default="flow-peak")
    parser.add_argument("--window-hours", type=float, default=12.0, help="Hours shown around peak flow in flow-peak mode.")
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
        "--rain-ts",
        rain_ts,
        "--rain-kind",
        rain_kind,
        "--out-png",
        str(out_png),
        "--auto-window-mode",
        args.auto_window_mode,
        "--window-hours",
        str(args.window_hours),
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
