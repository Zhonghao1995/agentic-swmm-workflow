"""Operational run-failure capture (runtime observability).

Why this module exists
----------------------
``negative_lessons`` records *modeling* failures — continuity FAILs,
diverged calibrations, non-physical parameter sets — so the agent avoids
re-proposing a known-bad region. It deliberately does NOT record
*operational* failures: an MCP child process that died mid-call, a tool
handed a path that does not resolve, a SWMM solver error. Yet those are
the failures that dominate real runs, and today they are invisible —
they scroll past in the trace and are never aggregated, so there is no
way to see the real failure distribution or drive fixes from data.

This module captures operational failures in a dedicated
``run_failures.jsonl`` store, kept *separate* from
``negative_lessons.jsonl`` so operational noise never pollutes the
modeling-knowledge recall path.

Backend
-------
JSONL — one line per recorded failure. Same contract as the other
memory stores: atomic append, tolerant of a torn final line on read,
missing file yields ``[]``. A clean run writes nothing (no empty file).

Schema (``SCHEMA_VERSION == "1.0"``)
------------------------------------
- ``run_id``: the run/session directory name — join key with the trace
- ``tool``: the tool whose call failed
- ``failure_class``: enumerated — see ``FAILURE_CLASSES``
- ``summary``: the (truncated) failure summary, verbatim from the tool
- ``recorded_at``: ISO 8601 UTC
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from agentic_swmm.memory.jsonl_store import append_rows, iter_rows
from typing import Any, Iterable


SCHEMA_VERSION = "1.0"

# Operational failure taxonomy. Distinct from negative_lessons' modeling
# ``lesson_type`` enum — these describe how the *runtime* broke, not what
# the *model* got wrong.
FAILURE_CLASSES = frozenset(
    {"mcp_transport", "path_resolution", "swmm_error", "tool_error"}
)

# Cap stored summaries so one pathological error string cannot bloat the
# store. The head carries the diagnostic signal; the tail is usually a
# repeated path or stack frame.
_SUMMARY_CAP = 300

_SWMM_ERROR_RE = re.compile(r"\bERROR\s+\d{3}\b")

_PATH_MARKERS = (
    "could not resolve",
    "must be an existing repository file",
    "must exist inside repository",
    "directory must exist",
    "file not found",
)


def _is_permission_denial(result: dict[str, Any]) -> bool:
    """Return True when a result is a user permission denial, not a fault.

    Denials carry ``ok=False`` but they are a deliberate user choice, not
    a runtime fault, so they must never be recorded as run failures.
    """
    perm = result.get("permission")
    if isinstance(perm, dict) and perm.get("approved") is False:
        return True
    # Fallback for stub results that predate the executor's permission seam.
    return str(result.get("summary", "")) == "tool not approved by user"


def classify_failure(result: dict[str, Any]) -> str | None:
    """Classify one tool-result dict into a failure class, or ``None``.

    Returns ``None`` when the result is not a recordable operational
    failure — i.e. it succeeded, or it is a user permission denial.
    """
    if result.get("ok", True):
        return None
    if _is_permission_denial(result):
        return None

    summary = str(result.get("summary", ""))
    low = summary.lower()

    if "mcp" in low and (
        "transport" in low
        or "process ended" in low
        or "tools/list" in low
        or "tools/call" in low
        or "unknown mcp server" in low
    ):
        return "mcp_transport"

    if any(marker in low for marker in _PATH_MARKERS):
        return "path_resolution"
    if "not found" in low and any(
        ext in low for ext in (".inp", ".out", ".rpt")
    ):
        return "path_resolution"

    if _SWMM_ERROR_RE.search(summary):
        return "swmm_error"

    return "tool_error"


@dataclass(frozen=True)
class RunFailure:
    """One row of operational run-failure memory."""

    run_id: str
    tool: str
    failure_class: str
    summary: str
    recorded_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-versioned dict written to disk."""
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "tool": self.tool,
            "failure_class": self.failure_class,
            "summary": self.summary,
            "recorded_at": self.recorded_at
            or datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        }


def resolve_store(memory_dir: Path | None = None) -> Path:
    """Return the ``run_failures.jsonl`` path.

    Mirrors ``audit_hook._resolve_memory_dir``'s env contract
    (``AISWMM_MEMORY_DIR``) so the operational store sits beside the
    modeling-memory stores by default.
    """
    if memory_dir is not None:
        return Path(memory_dir) / "run_failures.jsonl"
    override = os.environ.get("AISWMM_MEMORY_DIR")
    if override:
        return Path(override) / "run_failures.jsonl"
    return Path("memory/modeling-memory") / "run_failures.jsonl"


def record_run_failures(
    store: Path,
    run_id: str,
    results: Iterable[dict[str, Any]],
) -> list[RunFailure]:
    """Append every operational failure in ``results`` to ``store``.

    Scans ``results`` (the executor's per-tool result dicts), classifies
    each genuine failure (skipping successes and permission denials), and
    appends one JSONL row per failure. Returns the recorded failures.

    No-op (returns ``[]``, writes nothing) when there are no failures, so
    a clean run never creates the file.
    """
    failures: list[RunFailure] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        failure_class = classify_failure(result)
        if failure_class is None:
            continue
        summary = str(result.get("summary", ""))[:_SUMMARY_CAP]
        failures.append(
            RunFailure(
                run_id=run_id or "",
                tool=str(result.get("tool", "")),
                failure_class=failure_class,
                summary=summary,
            )
        )

    if not failures:
        return []

    store = Path(store)
    append_rows(store, (failure.to_dict() for failure in failures))
    return failures


def read_run_failures(store: Path) -> list[RunFailure]:
    """Return all recorded failures from ``store`` (``[]`` if missing).

    Tolerant of a torn final line from a concurrent append.
    """
    store = Path(store)
    if not store.is_file():
        return []
    out: list[RunFailure] = []
    for row in iter_rows(store):
        out.append(
            RunFailure(
                run_id=str(row.get("run_id", "")),
                tool=str(row.get("tool", "")),
                failure_class=str(row.get("failure_class", "")),
                summary=str(row.get("summary", "")),
                recorded_at=row.get("recorded_at"),
            )
        )
    return out
