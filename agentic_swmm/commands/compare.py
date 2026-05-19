"""``aiswmm compare`` — diff two SWMM runs (PRD-06 Phase B.1).

A pure CLI surface over :func:`agentic_swmm.agent.swmm_runtime.compare.compare_runs`.
Default output is a human-readable table; ``--json`` returns the
serialized :class:`RunComparison` so a downstream pipeline can post-
process the diff.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_swmm.agent.swmm_runtime.compare import (
    compare_runs,
    render_comparison_table,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "compare",
        help="Compare two SWMM runs on continuity metrics (PRD-06 B.1).",
    )
    parser.add_argument(
        "--run-a",
        type=Path,
        required=True,
        help="Path to run directory A.",
    )
    parser.add_argument(
        "--run-b",
        type=Path,
        required=True,
        help="Path to run directory B.",
    )
    parser.add_argument(
        "--metric",
        action="append",
        dest="metrics",
        default=None,
        help=(
            "Restrict the comparison to one or more named metrics. "
            "Repeatable. Defaults to runoff_continuity_pct + "
            "flow_continuity_pct."
        ),
    )
    parser.add_argument(
        "--benchmarks-path",
        type=Path,
        default=None,
        help=(
            "Optional override for the reference_benchmarks.yaml used "
            "to classify each metric. Defaults to the repo-shipped library."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the RunComparison as JSON on stdout instead of a table.",
    )
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    metrics = list(args.metrics) if args.metrics else None
    comparison = compare_runs(
        args.run_a,
        args.run_b,
        metrics=metrics,
        benchmarks_path=args.benchmarks_path,
    )
    if getattr(args, "json", False):
        print(json.dumps(comparison.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_comparison_table(comparison))
    # An "incomparable" verdict exits non-zero so a scripted pipeline can
    # detect the failure mode without parsing JSON. Other verdicts return 0
    # regardless of which run "won" — the verdict is informational, not an
    # error class.
    return 1 if comparison.verdict == "incomparable" else 0
