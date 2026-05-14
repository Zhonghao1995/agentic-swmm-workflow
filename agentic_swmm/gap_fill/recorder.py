"""Atomic recorder for gap-fill decisions (PRD-GF-CORE).

Two artefacts land on disk per recorded batch:

1. ``<run_dir>/09_audit/gap_decisions.json`` — append-mode JSON
   document. Shape::

       {
         "schema_version": "1",
         "decisions": [<GapDecision payload>, ...]
       }

   Writes go through a tmp-file + ``os.replace`` so a process killed
   mid-write leaves the previous payload intact (mirrors
   :mod:`agentic_swmm.hitl.decision_recorder`).

2. ``<run_dir>/09_audit/experiment_provenance.json`` —
   ``human_decisions[...]`` entries are appended (one per gap
   decision) via :func:`agentic_swmm.hitl.decision_recorder.append_decision`.
   The ``action`` field is ``"gap_fill_L1"`` or ``"gap_fill_L3"`` so a
   reviewer can grep the provenance ledger for gap-fill events. The
   ``decision_text`` carries a short, human-readable summary of the
   resolution (proposer source + final value).

The recorder mutates the input :class:`GapDecision` records to carry
``human_decisions_ref`` pointers back into the provenance ledger
before serialising — this keeps the cross-link bidirectional even
though ``GapDecision`` is a frozen dataclass (we materialise the
final payload as a dict, not a new dataclass instance).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from agentic_swmm.gap_fill.protocol import GapDecision
from agentic_swmm.hitl.decision_recorder import (
    append_decision,
    make_decision,
)


SCHEMA_VERSION = "1"


def _audit_dir(run_dir: Path) -> Path:
    """Return ``<run_dir>/09_audit`` and ensure it exists."""

    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)
    return audit


def _read_ledger(path: Path) -> dict[str, Any]:
    """Parse the gap-decisions ledger file or return an empty skeleton.

    A missing file, a corrupt JSON, or a non-dict top-level all map to
    the empty skeleton — the contract is "the ledger is always a valid
    JSON document", and a corrupt write would otherwise lock out
    further appends.
    """
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "decisions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "decisions": []}
    if not isinstance(payload, dict):
        return {"schema_version": SCHEMA_VERSION, "decisions": []}
    if not isinstance(payload.get("decisions"), list):
        payload["decisions"] = []
    payload["schema_version"] = SCHEMA_VERSION
    return payload


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` to ``path`` via tmp-file + ``os.replace``.

    Same pattern as ``decision_recorder._atomic_write_json``. We keep
    a private copy so the two modules stay independent (no churn-by-
    one-touching-the-other coupling).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _summarise_decision(decision: GapDecision) -> str:
    """Render a short human-readable summary for the provenance ledger.

    Captures the three facts a reviewer needs at a glance: severity,
    field name, and the resolved value. The proposer source goes on
    the same line so registry vs LLM vs human stays visible in the
    grep view.
    """
    return (
        f"{decision.severity} {decision.field}: "
        f"final_value={decision.final_value!r} "
        f"(source={decision.proposer.source}, "
        f"confidence={decision.proposer.confidence}, "
        f"decided_by={decision.decided_by})"
    )


def record_gap_decisions(
    run_dir: Path | str,
    decisions: list[GapDecision],
) -> list[GapDecision]:
    """Persist ``decisions`` and return the (cross-linked) list.

    For each :class:`GapDecision`:

    1. A matching ``human_decisions`` entry is appended to
       ``experiment_provenance.json`` (action ``"gap_fill_L1"`` /
       ``"gap_fill_L3"``).
    2. The gap-decisions ledger gains the decision payload with
       ``human_decisions_ref`` populated to point at the entry just
       created.

    The function does not raise on serialisation errors — it lets the
    caller see them. The provenance recorder is the one upstream
    component that must not blackhole errors (writes the canonical
    audit file).
    """
    run_dir_path = Path(run_dir)
    if not decisions:
        return list(decisions)

    audit = _audit_dir(run_dir_path)
    prov_path = audit / "experiment_provenance.json"
    ledger_path = audit / "gap_decisions.json"

    # Append matching human-decision entries first; the recorder
    # generates the IDs we need to cross-link from gap_decisions.json.
    enriched: list[GapDecision] = []
    for decision in decisions:
        hd = make_decision(
            action=f"gap_fill_{decision.severity}",
            pattern=f"gap_fill_{decision.severity}",
            evidence_ref="09_audit/gap_decisions.json",
            decision_text=_summarise_decision(decision),
        )
        append_decision(prov_path, hd)
        ref = (
            "09_audit/experiment_provenance.json#human_decisions/"
            f"id={hd.id}"
        )
        # GapDecision is frozen; rebuild with the populated ref.
        enriched.append(
            GapDecision(
                decision_id=decision.decision_id,
                gap_id=decision.gap_id,
                severity=decision.severity,
                field=decision.field,
                proposer=decision.proposer,
                proposed_value=decision.proposed_value,
                final_value=decision.final_value,
                proposer_overridden=decision.proposer_overridden,
                decided_by=decision.decided_by,
                decided_at=decision.decided_at,
                resume_mode=decision.resume_mode,
                human_decisions_ref=ref,
            )
        )

    ledger = _read_ledger(ledger_path)
    for decision in enriched:
        ledger["decisions"].append(decision.to_dict())
    _atomic_write(ledger_path, ledger)
    return enriched


def read_gap_decisions(run_dir: Path | str) -> list[GapDecision]:
    """Return the list of :class:`GapDecision` recorded under ``run_dir``.

    Missing ledger / corrupt JSON yield an empty list — symmetric with
    the writer's tolerance and consistent with the contract used by
    ``decision_recorder.read_decisions``.
    """
    run_dir_path = Path(run_dir)
    ledger_path = run_dir_path / "09_audit" / "gap_decisions.json"
    if not ledger_path.is_file():
        return []
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    raw = payload.get("decisions")
    if not isinstance(raw, list):
        return []
    out: list[GapDecision] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(GapDecision.from_dict(entry))
        except ValueError:
            # Skip malformed entries rather than failing the whole
            # read — a partially-corrupt ledger is still useful and a
            # strict failure would block later appends.
            continue
    return out


__all__ = ["record_gap_decisions", "read_gap_decisions", "SCHEMA_VERSION"]
