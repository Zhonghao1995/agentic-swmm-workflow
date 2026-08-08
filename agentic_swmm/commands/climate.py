"""``aiswmm climate`` — batch a model under climate-forcing scenarios.

The calibrate-then-force loop (ADR-0010): calibrate a model against
observed data first (``aiswmm calibrate``), then compare its response
under precipitation-scaled climate scenarios. This command owns the
second half. It accepts any INP (typically the calibrated model) and,
optionally, a calibration ``--params-json`` + ``--patch-map`` pair to
apply best parameters onto the base INP before scaling, via the same
``inp_patch`` skill script the calibration loop uses (ADR-0005: the
patch-map is the only parameter contract).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.agent.swmm_runtime import run_layout
from agentic_swmm.utils.paths import repo_root, require_file, script_path
from agentic_swmm.utils.subprocess_runner import python_command, run_command

_CLIMATE_EXAMPLE = (
    'aiswmm climate --inp runs/agent/canada-live/05_builder/model.inp '
    '--factors "1.0,1.1,1.2,1.35"'
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "climate",
        help="Batch-run precipitation-scaled climate scenarios and compare responses.",
    )
    parser.add_argument("--inp", required=True, type=Path, help="Base model (typically the calibrated INP).")
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Run directory (default: runs/climate/<inp-stem>-<timestamp>).",
    )
    parser.add_argument(
        "--factors",
        default=None,
        help=(
            'Comma-separated precipitation multipliers, e.g. "1.0,1.1,1.2". '
            "Default: 1.0, 1.10, 1.20, 1.35."
        ),
    )
    parser.add_argument("--node", default=None, help="Report node (default: the INP's first outfall).")
    parser.add_argument(
        "--params-json",
        type=Path,
        default=None,
        help="Calibrated parameter values (JSON object) to apply before scaling.",
    )
    parser.add_argument(
        "--patch-map",
        type=Path,
        default=None,
        help="Patch-map JSON mapping parameter names to INP edit sites (required with --params-json).",
    )
    parser.add_argument("--json", action="store_true", help="Print the machine-readable summary.")
    register_example_flag(parser, example_text=_CLIMATE_EXAMPLE)
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    from agentic_swmm.agent.swmm_runtime.climate_scenarios import (
        DEFAULT_SCENARIOS,
        parse_factors,
        run_climate_batch,
    )

    try:
        inp = require_file(args.inp, "INP file")
    except FileNotFoundError as exc:
        print(f"error: {exc}")
        return 2

    if bool(args.params_json) != bool(args.patch_map):
        print("error: --params-json and --patch-map must be given together.")
        return 2

    try:
        scenarios = parse_factors(args.factors) if args.factors else DEFAULT_SCENARIOS
    except ValueError as exc:
        print(f"error: bad --factors: {exc}")
        return 2

    run_dir = args.run_dir or (
        repo_root() / "runs" / "climate" / f"{inp.stem}-{int(time.time())}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    base_inp = inp
    if args.params_json:
        base_inp = _apply_calibration_params(
            inp, args.params_json, args.patch_map, run_dir
        )
        if isinstance(base_inp, int):
            return base_inp
        print(f"calibrated parameters applied: {base_inp}")

    result = run_climate_batch(
        base_inp=base_inp,
        run_dir=run_dir,
        scenarios=scenarios,
        node=args.node,
        progress=lambda text: print(f"  {text}"),
    )

    if args.json:
        print(Path(result.summary_json).read_text(encoding="utf-8"))
    else:
        print()
        print(Path(result.summary_md).read_text(encoding="utf-8"))
        print(f"Summary: {result.summary_json}")
        print(f"Run dir: {result.run_dir}")
    return 0 if result.ok else 2


def _apply_calibration_params(
    inp: Path, params_json: Path, patch_map: Path, run_dir: Path
) -> Path | int:
    """Patch calibrated parameters onto ``inp`` via the skill script."""
    try:
        require_file(params_json, "params JSON")
        require_file(patch_map, "patch-map JSON")
    except FileNotFoundError as exc:
        print(f"error: {exc}")
        return 2
    out = run_layout.stage_dir(run_dir, run_layout.CLIMATE, create=True) / "calibrated_model.inp"
    script = script_path("skills", "swmm-calibration", "scripts", "inp_patch.py")
    command = python_command(
        script,
        "--inp",
        str(inp),
        "--patch-map",
        str(patch_map),
        "--params",
        str(params_json),
        "--out",
        str(out),
    )
    result = run_command(command, check=False)
    if result.return_code != 0 or not out.is_file():
        print(f"error: applying calibrated parameters failed: {result.stderr[-800:]}")
        return 2
    return out


__all__ = ["register", "main"]
