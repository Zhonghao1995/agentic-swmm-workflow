"""``aiswmm calibrate`` — checkpoint-aware calibration loop (PRD-06 C.5).

This is the top-level CLI surface for the thin
:func:`agentic_swmm.agent.swmm_runtime.calibration_runner.run_calibration_with_checkpoints`
facade. It is intentionally narrow: the command does not (yet)
delegate to spotpy / SCE-UA / DREAM-ZS. It demonstrates the
checkpoint contract over the facade's default iterator so the agent
and the user have a way to validate the wiring before hooking it to
the real solver in a follow-up round.

Why surface this now:
- The PRD calls for a ``--progress`` flag on a calibrate command.
- The facade already writes ``progress.json`` correctly; without a
  user-facing command, no one can observe it from the shell.

Output policy:
- TTY: print one ``summarize_progress`` line every ``--print-every``
  iterations to stdout.
- Non-TTY: append the same line to ``<run-dir>/agent_trace.jsonl`` as
  a structured JSON record (so log scrapers do not have to parse
  prose).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agentic_swmm.agent.flag_naming import (
    register_example_flag,
    register_inp_flag,
)
from agentic_swmm.agent.help_router import WidthSafeFormatter
from agentic_swmm.agent.honesty import STUB_BANNER, fail_fast_if_path_missing
from agentic_swmm.agent.swmm_runtime.calibration_runner import (
    CalibrationRunConfig,
    run_calibration_with_checkpoints,
)
from agentic_swmm.memory.run_progress import (
    ProgressCheckpoint,
    summarize_progress,
)


_CALIBRATE_EXAMPLE = (
    "aiswmm calibrate --inp model.inp --run-id calib_001 "
    "--total-iters 100 --param manning_n=0.010,0.018 "
    "--run-dir runs/calib_001"
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "calibrate",
        help=(
            "Run a checkpoint-aware calibration loop. Writes "
            "progress.json every N iterations and (with --progress) "
            "prints one-line summaries."
        ),
        # PRD-08 Phase B (audit #26): width-safe usage formatter so
        # ``--total-iters TOTAL_ITERS`` (and the other required flags
        # below) never wrap mid-flag.
        formatter_class=WidthSafeFormatter,
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Stable run id stamped into every checkpoint.",
    )
    parser.add_argument(
        "--algorithm",
        default="sceua",
        choices=("sceua", "dream_zs"),
        help="Calibration algorithm label (default sceua).",
    )
    parser.add_argument(
        "--total-iters",
        type=int,
        required=True,
        help="Total iterations to drive the loop for.",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=1,
        help="Write progress.json every N iterations (default 1).",
    )
    # PRD-08 A.2: ``--inp`` is the canonical flag; ``--base-inp`` is the
    # deprecated alias kept for one release. Both populate ``args.inp``
    # via :func:`register_inp_flag`.
    register_inp_flag(
        parser,
        required=True,
        help_text=(
            "INP path (stamped into config; not opened by the stub)."
        ),
    )
    parser.add_argument(
        "--observed-csv",
        type=Path,
        default=None,
        help=(
            "Observed CSV path (stamped into config; not opened by the "
            "stub). Optional while the synthetic walker is in place."
        ),
    )
    parser.add_argument(
        "--param",
        action="append",
        required=True,
        metavar="NAME=LOW,HIGH",
        help=(
            "Parameter to perturb. Repeatable. Example: --param manning_n=0.01,0.03"
        ),
    )
    parser.add_argument(
        "--objective",
        default="nse",
        choices=("nse", "kge", "rmse"),
        help="Objective name (default nse). RMSE is min; others are max.",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Directory to write progress.json (and trace JSONL) into.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help=(
            "Print one summarize_progress line per --print-every "
            "iterations. Full checkpoints written to "
            "<run-dir>/progress.json; tracing also lands in "
            "<run-dir>/agent_trace.jsonl."
        ),
    )
    parser.add_argument(
        "--print-every",
        type=int,
        default=1,
        help=(
            "When --progress is set, emit a line every N checkpointed "
            "iterations (default 1)."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress the STUB banner. The banner is emitted while the "
            "real solver hookup is pending; --quiet hides it for "
            "scripted callers that already know."
        ),
    )
    # PRD-08 A.2: every verb learns ``--example`` so a user can paste a
    # known-working invocation without leaving the terminal.
    register_example_flag(parser, example_text=_CALIBRATE_EXAMPLE)
    parser.set_defaults(func=main)


def _parse_param_specs(specs: list[str]) -> list[tuple[str, float, float]]:
    parameters: list[tuple[str, float, float]] = []
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"--param spec must be NAME=LOW,HIGH; got {spec!r}")
        name, bounds_text = spec.split("=", 1)
        name = name.strip()
        if not name:
            raise ValueError(f"--param spec has empty NAME: {spec!r}")
        if "," not in bounds_text:
            raise ValueError(f"--param bounds must be LOW,HIGH; got {bounds_text!r}")
        low_text, high_text = bounds_text.split(",", 1)
        try:
            low = float(low_text)
            high = float(high_text)
        except ValueError as exc:
            raise ValueError(f"--param {name}: bounds must be numeric ({exc})") from exc
        if high < low:
            raise ValueError(f"--param {name}: LOW ({low}) must be <= HIGH ({high})")
        parameters.append((name, low, high))
    return parameters


def _is_tty() -> bool:
    return sys.stdout.isatty()


def _append_trace_line(run_dir: Path, payload: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    trace = run_dir / "agent_trace.jsonl"
    with trace.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def main(args: argparse.Namespace) -> int:
    # PRD-08 A.1 (audit #2): print the STUB banner to stdout before any
    # numbers appear so a domain user cannot mistake the synthetic walker
    # for a real calibration. --quiet suppresses it for scripted callers.
    if not getattr(args, "quiet", False):
        print(STUB_BANNER)

    # PRD-08 A.1 (audit #2): the synthetic walker historically claimed
    # success even when --inp pointed at a non-existent file.
    # Refuse to start with an actionable stderr error.
    fail_fast_if_path_missing(args.inp, "--inp")

    try:
        parameters = _parse_param_specs(args.param)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    cfg = CalibrationRunConfig(
        run_id=args.run_id,
        algorithm=args.algorithm,
        total_iters=args.total_iters,
        base_inp=args.inp,
        observed_csv=args.observed_csv,
        parameters=parameters,
        objective=args.objective,
        checkpoint_every=args.checkpoint_every,
    )

    run_dir: Path = args.run_dir
    print_every: int = max(1, args.print_every)
    counter = {"n": 0}

    def _on_progress(ckpt: ProgressCheckpoint) -> None:
        counter["n"] += 1
        if not args.progress:
            return
        if counter["n"] % print_every != 0:
            return
        line = summarize_progress(ckpt)
        if _is_tty():
            print(line)
        else:
            _append_trace_line(
                run_dir,
                {
                    "event": "calibrate_progress",
                    "run_id": ckpt.run_id,
                    "iter_index": ckpt.iter_index,
                    "total_iters": ckpt.total_iters,
                    "best_objective_so_far": ckpt.best_objective_so_far,
                    "wall_time_s": ckpt.wall_time_s,
                    "summary": line,
                },
            )

    result = run_calibration_with_checkpoints(
        cfg,
        run_dir,
        progress_callback=_on_progress,
    )

    summary = {
        "ok": not result.errors,
        "run_id": result.run_id,
        "algorithm": result.algorithm,
        "iterations_completed": result.iterations_completed,
        "total_iters": result.total_iters,
        "best_objective": result.best_objective,
        "best_parameters": result.best_parameters,
        "wall_time_s": result.wall_time_s,
        "errors": result.errors,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not result.errors else 2
