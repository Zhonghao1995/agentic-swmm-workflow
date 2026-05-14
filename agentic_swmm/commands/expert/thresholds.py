"""``aiswmm thresholds override <run_dir> <name> <value>`` (PRD-Z).

Records a human-authored ``thresholds_override`` decision: the modeller
overrides a HITL threshold value for one specific run. The threshold
name and the new value land in ``decision_text`` so the override is
visible in the audit-note ``## Human Decisions`` table.
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
        "thresholds",
        help=(
            "Expert-only: override HITL thresholds for a single run. "
            "Subcommand 'override' records the override as a "
            "human_decisions entry."
        ),
    )
    inner = parser.add_subparsers(dest="thresholds_command", required=True)
    override = inner.add_parser(
        "override",
        help="Record a one-off override of a threshold value for this run.",
    )
    override.add_argument(
        "run_dir",
        type=Path,
        help="Path to the run directory receiving the override decision.",
    )
    override.add_argument(
        "name",
        help="Name of the threshold being overridden (see docs/hitl-thresholds.md).",
    )
    override.add_argument(
        "value",
        help="New threshold value to record (kept as a string in provenance).",
    )
    override.add_argument(
        "--note",
        default=None,
        help="Optional free-text note saved alongside the override record.",
    )
    override.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir.resolve()
    provenance = resolve_provenance_path(run_dir, require_exists=True)
    if provenance is None:
        return 2
    decision_text = f"threshold {args.name} overridden to {args.value}"
    if args.note:
        decision_text = f"{decision_text}; {args.note}"
    record_and_print(
        provenance,
        action="thresholds_override",
        evidence_ref=evidence_ref_for(run_dir, provenance),
        decision_text=decision_text,
        pattern=args.name,
    )
    return 0
