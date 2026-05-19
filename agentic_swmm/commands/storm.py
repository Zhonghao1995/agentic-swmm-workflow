"""``aiswmm storm`` — algorithmic design-storm generator (PRD-06 B.4).

A pure CLI surface over
:func:`agentic_swmm.agent.swmm_runtime.design_storm.generate_design_storm`.
Writes a SWMM ``[TIMESERIES]`` block to ``--out`` (file) or stdout
when ``--out`` is omitted.

Scope intentionally matches the verb: no IDF/curve-number lookup,
just the shape primitives a modeler reaches for interactively.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentic_swmm.agent.swmm_runtime.design_storm import (
    generate_design_storm,
    to_swmm_dat,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "storm",
        help="Generate an algorithmic design storm in SWMM DAT format (PRD-06 B.4).",
    )
    parser.add_argument(
        "--depth-mm",
        type=float,
        required=True,
        help="Total rainfall depth in millimetres.",
    )
    parser.add_argument(
        "--duration-min",
        type=int,
        required=True,
        help=(
            "Storm duration in minutes. Must be a positive multiple of "
            "--interval-min so the last step is not truncated."
        ),
    )
    parser.add_argument(
        "--shape",
        choices=("uniform", "triangular", "front_loaded", "back_loaded"),
        default="uniform",
        help=(
            "Hyetograph shape. 'triangular' peaks at the midpoint; "
            "'front_loaded' peaks at 25%%; 'back_loaded' peaks at 75%%."
        ),
    )
    parser.add_argument(
        "--interval-min",
        type=int,
        default=5,
        help="Time step of the hyetograph in minutes (default 5).",
    )
    parser.add_argument(
        "--start-time",
        type=str,
        default="2000-01-01 00:00",
        help="Storm start time, YYYY-MM-DD HH:MM (default '2000-01-01 00:00').",
    )
    parser.add_argument(
        "--station-id",
        type=str,
        default="STN1",
        help="SWMM timeseries station identifier (default 'STN1').",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Write the [TIMESERIES] block here. When omitted, the "
            "block is printed to stdout so a shell redirect still works."
        ),
    )
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    try:
        storm = generate_design_storm(
            depth_mm=args.depth_mm,
            duration_min=args.duration_min,
            shape=args.shape,
            interval_min=args.interval_min,
            start_time=args.start_time,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    text = to_swmm_dat(storm, station_id=args.station_id)
    if args.out is None:
        sys.stdout.write(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(
            f"wrote {len(storm.intensities_mm_per_hr)}-step design storm "
            f"to {args.out}"
        )
    return 0
