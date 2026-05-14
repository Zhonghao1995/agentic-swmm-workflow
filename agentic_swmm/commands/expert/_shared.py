"""Helpers shared by the four expert-only CLI subcommands (PRD-Z).

Each subcommand is small (parse args, validate path, append a
``human_decisions`` record, print a one-line confirmation) but the
plumbing for resolving the provenance path, requiring the file to
exist, and rendering the success JSON is identical. This module keeps
that boilerplate in one place so the subcommands read as small declarative
files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agentic_swmm.hitl.decision_recorder import HumanDecision, append_decision, make_decision


def resolve_provenance_path(run_dir: Path, *, require_exists: bool) -> Path | None:
    """Return ``<run_dir>/09_audit/experiment_provenance.json`` or ``None``.

    When ``require_exists`` is true and the file is missing, the
    function prints a clear stderr message and returns ``None`` so the
    caller can ``return 2`` (or similar). This is the seam ``aiswmm
    publish`` uses to refuse runs that have never been audited.
    """
    if not run_dir.is_dir():
        print(
            f"error: run_dir is not a directory: {run_dir}",
            file=sys.stderr,
        )
        return None
    candidate = run_dir / "09_audit" / "experiment_provenance.json"
    if require_exists and not candidate.is_file():
        print(
            f"error: experiment_provenance.json missing for run: {candidate}. "
            "Run `aiswmm audit --run-dir <run_dir>` first.",
            file=sys.stderr,
        )
        return None
    return candidate


def record_and_print(
    provenance_path: Path,
    *,
    action: str,
    evidence_ref: str,
    decision_text: str | None = None,
    pattern: str | None = None,
) -> dict[str, Any]:
    """Append a :class:`HumanDecision` and print a JSON summary.

    The JSON summary on stdout is the contract for tests and downstream
    tooling — ``ok: bool, action: str, decision_id: str, by: str,
    provenance: str``. Returns the summary so call-sites can return it
    from ``argparse`` ``main`` for symmetric typing.
    """
    decision = make_decision(
        action=action,
        evidence_ref=evidence_ref,
        decision_text=decision_text,
        pattern=pattern,
    )
    append_decision(provenance_path, decision)
    summary = {
        "ok": True,
        "action": action,
        "decision_id": decision.id,
        "by": decision.by,
        "provenance": str(provenance_path),
    }
    print(json.dumps(summary, indent=2))
    return summary


def evidence_ref_for(run_dir: Path, provenance_path: Path) -> str:
    """Return ``evidence_ref`` relative to ``run_dir``.

    Expert CLI invocations record the run's own provenance file as the
    evidence reference, since the action is "I, the human, decided X
    about this run." A relative path keeps the entry portable across
    machines.
    """
    try:
        return str(provenance_path.relative_to(run_dir))
    except ValueError:
        return str(provenance_path)
