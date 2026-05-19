"""Transparency log for memory-consulting decisions (PRD-07 Phase 2).

Every time the runtime consults memory before making a decision, it
should leave a single JSONL line behind so the user (and later, the
paper reviewer) can see *why* the agent did what it did. The schema
is deliberately small — one line per decision point, with enough
context to reconstruct the call without re-running it.

Where the log lives
-------------------
Right next to the existing audit artefacts inside the run dir
(``<run_dir>/memory_trace.jsonl``). The location mirrors how
``llm_calls.jsonl`` ships per-run rather than per-project — a memory
trace tied to one run can be archived or pruned with the run, while
a project-level log would couple every audit to the whole project's
history.

Failure modes
-------------
- Missing run dir → directory is created on first write.
- Torn final line (concurrent reader during a writer's flush) → the
  reader silently skips, identical to
  :func:`agentic_swmm.memory.parametric_memory.recall_parametric`.
- Invalid confidence label → :class:`ValueError` at write time. The
  whitelist is intentional: it forces the runtime author to assign a
  decision to one of the 4 quadrants (stakes × evidence) rather than
  inventing a new fuzzy label per call site.

This module is consumed by the audit hook (one wiring call site lands
in PRD-07 Phase 2 already, to prove the contract holds end-to-end)
and will be consumed by the disambiguator and the QA threshold
replacement in Phase 3 / Phase 4.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent.memory_context import MemoryContext


MEMORY_TRACE_FILENAME = "memory_trace.jsonl"
SCHEMA_VERSION = "1.0"

VALID_CONFIDENCE_LABELS: tuple[str, ...] = (
    "auto_complete",
    "memory_informed",
    "llm",
    "hitl",
)


@dataclass(frozen=True)
class MemoryTraceEntry:
    """In-memory representation of one trace line.

    The dataclass exists so callers that synthesise an entry in
    tests don't have to hand-roll a dict; it is also the natural
    shape the disambiguator/QA-replacement will produce when they
    land in Phase 3/4.
    """

    timestamp: str
    decision_point: str
    memory_context_summary: str
    parametric_hit_count: int
    thresholds_used: list[str]
    decision_taken: str
    confidence: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "decision_point": self.decision_point,
            "memory_context_summary": self.memory_context_summary,
            "parametric_hit_count": self.parametric_hit_count,
            "thresholds_used": list(self.thresholds_used),
            "decision_taken": self.decision_taken,
            "confidence": self.confidence,
            "schema_version": self.schema_version,
        }


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def log_memory_decision(
    *,
    run_dir: Path,
    decision_point: str,
    context: MemoryContext,
    decision: str,
    confidence: str,
) -> Path:
    """Append one decision line to ``<run_dir>/memory_trace.jsonl``.

    Arguments:
        run_dir: Per-run directory (created if missing).
        decision_point: Short stable label identifying the call site
            (e.g. ``"audit_hook_parametric_write"``,
            ``"plot_node_default"``). Used by the next agent to filter
            specific call sites.
        context: The :class:`MemoryContext` consulted just before the
            decision. Only its summary + hit count + threshold keys
            are persisted; the full parametric records stay in the
            store they came from.
        decision: Plain string the runtime chose (a value, an action
            name, etc.). We do not encode JSON-of-JSON; if the
            decision is structured, the caller should serialise it.
        confidence: One of :data:`VALID_CONFIDENCE_LABELS`. Anything
            else raises :class:`ValueError` so the call site is
            forced to pick a quadrant.

    Returns:
        Path to the JSONL file (so callers can attach it to result
        dicts without recomputing the path).
    """
    if confidence not in VALID_CONFIDENCE_LABELS:
        raise ValueError(
            "confidence must be one of "
            f"{VALID_CONFIDENCE_LABELS}; got {confidence!r}"
        )

    run_dir_path = Path(run_dir)
    run_dir_path.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir_path / MEMORY_TRACE_FILENAME

    entry = MemoryTraceEntry(
        timestamp=_now_iso(),
        decision_point=decision_point,
        memory_context_summary=context.summary,
        parametric_hit_count=context.parametric_hit_count,
        thresholds_used=sorted(context.reference_thresholds.keys()),
        decision_taken=decision,
        confidence=confidence,
    )

    line = json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True)
    # Single ``open(..., "a")`` write so a concurrent reader either
    # sees a complete line or sees nothing for this entry — same
    # contract as parametric_memory.
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return trace_path


def read_memory_trace(run_dir: Path) -> list[dict[str, Any]]:
    """Return all decision entries from ``<run_dir>/memory_trace.jsonl``.

    Missing file yields ``[]`` (not an error); a torn final line is
    skipped silently. Both behaviours match the parametric memory
    reader so the runtime never has to special-case "trace not
    initialised yet".
    """
    trace_path = Path(run_dir) / MEMORY_TRACE_FILENAME
    if not trace_path.is_file():
        return []

    entries: list[dict[str, Any]] = []
    with trace_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                # Torn final line during a concurrent write — skip.
                continue
    return entries


__all__ = [
    "MEMORY_TRACE_FILENAME",
    "SCHEMA_VERSION",
    "VALID_CONFIDENCE_LABELS",
    "MemoryTraceEntry",
    "log_memory_decision",
    "read_memory_trace",
]
