"""``aiswmm publish <run_dir>`` (PRD-Z).

Marks a run as publication-ready. The command refuses if the run has
never been audited (no ``09_audit/experiment_provenance.json``) — the
publish gesture is the modeller's signature on a *recorded* artefact,
not a wishful one.
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
        "publish",
        help=(
            "Expert-only: mark a run as publication-ready. Requires an "
            "existing experiment_provenance.json — run `aiswmm audit` first."
        ),
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to the run directory being marked publication-ready.",
    )
    parser.add_argument(
        "--note",
        default=None,
        help="Optional free-text note saved with the human_decisions record.",
    )
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir.resolve()
    provenance = resolve_provenance_path(run_dir, require_exists=True)
    if provenance is None:
        return 2
    record_and_print(
        provenance,
        action="publish",
        evidence_ref=evidence_ref_for(run_dir, provenance),
        decision_text=args.note,
    )
    return 0
