"""``aiswmm pour_point confirm <case_id> [--run-dir <run_dir>]`` (PRD-Z).

Records a human-authored ``pour_point_confirm`` decision: the modeller
confirms that the pour point identified by the GIS QA pipeline is
hydrologically reasonable. The case ID is recorded in the
``decision_text`` so a later auditor can correlate the confirmation
with the GIS QA artefact.
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
        "pour_point",
        help=(
            "Expert-only: confirm pour-point sanity. Subcommand 'confirm' "
            "records human authority over the pour-point selection."
        ),
    )
    inner = parser.add_subparsers(dest="pour_point_command", required=True)
    confirm = inner.add_parser(
        "confirm",
        help=(
            "Record that the human modeller confirms the pour point "
            "for the given case is hydrologically reasonable."
        ),
    )
    confirm.add_argument(
        "case_id",
        help="Identifier of the GIS case whose pour point is being confirmed.",
    )
    confirm.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help=(
            "Path to the run directory whose experiment_provenance.json will "
            "receive the human_decisions entry."
        ),
    )
    confirm.add_argument(
        "--note",
        default=None,
        help="Optional free-text note saved with the human_decisions record.",
    )
    confirm.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir.resolve()
    provenance = resolve_provenance_path(run_dir, require_exists=True)
    if provenance is None:
        return 2
    decision_text = f"case_id={args.case_id}"
    if args.note:
        decision_text = f"{decision_text}; {args.note}"
    record_and_print(
        provenance,
        action="pour_point_confirm",
        evidence_ref=evidence_ref_for(run_dir, provenance),
        decision_text=decision_text,
        pattern="pour_point_suspect",
    )
    return 0
