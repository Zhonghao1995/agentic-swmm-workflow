"""``aiswmm calibration accept <run_dir>`` (PRD-Z).

Records a human-authored ``calibration_accept`` decision on the target
run's ``experiment_provenance.json``. This is the *only* path through
which a calibrated parameter set is promoted to canonical — the agent
cannot call it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agentic_swmm.commands.expert._shared import (
    evidence_ref_for,
    record_and_print,
    resolve_provenance_path,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "calibration",
        help=(
            "Expert-only: promote calibration outcomes. "
            "Subcommand 'accept' records human authority over the calibrated "
            "parameters."
        ),
    )
    inner = parser.add_subparsers(dest="calibration_command", required=True)
    accept = inner.add_parser(
        "accept",
        help="Record that the human modeller accepts the calibration for a run.",
    )
    accept.add_argument(
        "run_dir",
        type=Path,
        help="Path to the run directory whose calibration is being accepted.",
    )
    accept.add_argument(
        "--note",
        default=None,
        help="Optional free-text note saved with the human_decisions record.",
    )
    accept.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir.resolve()
    provenance = resolve_provenance_path(run_dir, require_exists=True)
    if provenance is None:
        return 2
    record_and_print(
        provenance,
        action="calibration_accept",
        evidence_ref=evidence_ref_for(run_dir, provenance),
        decision_text=args.note,
    )
    return 0
