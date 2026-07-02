"""Parametric memory: per-run quantitative records (PRD-06 Phase A.1).

A modeler thinks in *parameters and metrics*, not in text patterns.
``lessons_learned.md`` catalogs prose lessons; this module catalogs the
quantitative facts about each SWMM run so the agent can answer "what
Manning's *n* did I use last project" or "what continuity did the LID
intervention produce" without grepping prose.

Backend
-------
JSONL — one line per run, append-only — is the canonical source of
truth. Reads stream the file linearly for small stores; once the file
exceeds ~1k rows, ``recall_parametric`` transparently builds a SQLite
sidecar index next to the JSONL and queries that instead. The sidecar
is derived; deleting it triggers a rebuild on the next read.

Schema (``schema_version == "2.0"``)
-----------------------------------
- ``run_id``: stable identifier (matches ``experiment_provenance.json``)
- ``case_name``: human label
- ``swmm_version``: e.g. ``"5.2.4"``; needed for cross-version refusal
  later (Phase C)
- ``model_structure``: routing / infiltration / time_step / duration
- ``qa_metrics``: continuity %, mass-balance % (from .rpt)
- ``performance_metrics``: NSE / KGE / PBIAS / peak errors (when
  calibrated). ``None`` is permitted for uncalibrated runs.
- ``watershed_classification``: size_km2 / impervious_pct / climate /
  land_use_dominant. ``None`` permitted when not yet classified.
- ``calibration_status``: one of ``uncalibrated`` /
  ``calibrated_against_observed`` / ``validation_only`` / ``None``.
- ``parameter_set_ref``: pointer into ``calibration_memory`` (e.g.
  ``calibration_memory/run_42``) when the run reused a known set.
- ``evidence_runs_count``: how many runs contributed to this row. A
  single per-run write is ``1``; a calibration batch flushing a
  consolidated row writes the number of iterations.
- ``recorded_utc``: ISO-8601 timestamp

The 1.0 -> 2.0 bump only added optional fields; legacy callers that
constructed a 1.0 ``ParametricRecord`` continue to compile, and on-disk
1.0 rows surface as 2.0 records via ``version_compat.migrate_record``.

Failure mode
------------
Atomic-append: each call opens with ``"a"`` and writes a single
JSON-encoded line. Concurrent readers may observe a torn final line but
earlier records are intact. This mirrors the
``llm_calls.jsonl`` contract elsewhere in the audit pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from agentic_swmm.memory.jsonl_store import append_row, iter_rows
from typing import Any


SCHEMA_VERSION = "2.0"

#: Permitted values for :attr:`ParametricRecord.calibration_status`.
#: ``None`` is also permitted (the run never declared a status).
CALIBRATION_STATUS_VALUES = frozenset(
    {
        "uncalibrated",
        "calibrated_against_observed",
        "validation_only",
    }
)


@dataclass(frozen=True)
class ParametricRecord:
    """One row of parametric memory.

    Frozen so callers can pass the same record through multiple
    helpers without aliasing. Optional fields default to empty dicts
    so the writer never barfs on a half-populated run (some metrics
    are only available post-calibration).
    """

    run_id: str
    case_name: str
    swmm_version: str | None = None
    model_structure: dict[str, Any] = field(default_factory=dict)
    qa_metrics: dict[str, Any] = field(default_factory=dict)
    performance_metrics: dict[str, Any] = field(default_factory=dict)
    watershed_classification: dict[str, Any] = field(default_factory=dict)
    calibration_status: str | None = None
    parameter_set_ref: str | None = None
    evidence_runs_count: int = 1
    recorded_utc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-versioned dict written to disk."""
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "case_name": self.case_name,
            "swmm_version": self.swmm_version,
            "model_structure": dict(self.model_structure),
            "qa_metrics": dict(self.qa_metrics),
            "performance_metrics": dict(self.performance_metrics),
            "watershed_classification": dict(self.watershed_classification),
            "calibration_status": self.calibration_status,
            "parameter_set_ref": self.parameter_set_ref,
            "evidence_runs_count": int(self.evidence_runs_count),
            "recorded_utc": self.recorded_utc
            or datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
        }
        return payload


def record_parametric_run(store_path: Path, record: ParametricRecord) -> None:
    """Append ``record`` to the JSONL store at ``store_path``.

    Creates parent directories if needed. The write is a single
    ``open(..., "a")`` call so concurrent readers always see complete
    earlier lines.

    Validation (Round 5 / schema 2.0):
        - ``run_id`` and ``case_name`` must be non-empty (legacy 1.0).
        - ``calibration_status``, if not ``None``, must be one of
          :data:`CALIBRATION_STATUS_VALUES`.
        - ``watershed_classification`` and ``performance_metrics`` must
          be dict-shaped. ``None`` is *not* a valid input on the
          dataclass (the field defaults to ``{}``); but a caller that
          explicitly passes a non-dict gets a clean rejection rather
          than a downstream KeyError.
        - ``evidence_runs_count`` must be a positive int.

    Raises:
        ValueError: any of the above invariants is violated.
    """
    if not record.run_id or not record.run_id.strip():
        raise ValueError("ParametricRecord.run_id must be a non-empty string")
    if not record.case_name or not record.case_name.strip():
        raise ValueError("ParametricRecord.case_name must be a non-empty string")

    if (
        record.calibration_status is not None
        and record.calibration_status not in CALIBRATION_STATUS_VALUES
    ):
        raise ValueError(
            "ParametricRecord.calibration_status must be one of "
            f"{sorted(CALIBRATION_STATUS_VALUES)} or None; got "
            f"{record.calibration_status!r}"
        )

    if not isinstance(record.watershed_classification, dict):
        raise ValueError(
            "ParametricRecord.watershed_classification must be a dict "
            f"(got {type(record.watershed_classification).__name__})"
        )
    if not isinstance(record.performance_metrics, dict):
        raise ValueError(
            "ParametricRecord.performance_metrics must be a dict "
            f"(got {type(record.performance_metrics).__name__})"
        )

    if not isinstance(record.evidence_runs_count, int) or isinstance(
        record.evidence_runs_count, bool
    ):
        raise ValueError(
            "ParametricRecord.evidence_runs_count must be an int "
            f"(got {type(record.evidence_runs_count).__name__})"
        )
    if record.evidence_runs_count < 1:
        raise ValueError(
            "ParametricRecord.evidence_runs_count must be >= 1 "
            f"(got {record.evidence_runs_count})"
        )

    store_path = Path(store_path)
    payload = record.to_dict()
    append_row(store_path, payload)


def recall_parametric(
    store_path: Path, filters: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Return all rows from ``store_path`` matching ``filters``.

    ``filters`` keys may use dotted notation to address nested fields
    (``"qa_metrics.runoff_continuity_pct"``,
    ``"watershed_classification.size_km2"``,
    ``"performance_metrics.nse"``). Values match by equality today;
    range queries are a Phase B extension. A missing or empty filter
    dict returns all rows.

    Missing files yield ``[]`` (not an error) so first-time callers
    do not have to special-case a fresh project.

    SQLite acceleration (PRD-06 §4.1)
    ---------------------------------
    When the JSONL has grown past the index threshold (default 1k
    rows), this verb transparently builds or refreshes a SQLite sidecar
    next to the JSONL and queries through it. The JSONL stays the
    canonical source of truth; the sidecar is derived and can be
    deleted safely (it just rebuilds on the next read). For smaller
    stores the linear-scan path stays — the SQLite cost only buys back
    over many rows.
    """
    from agentic_swmm.memory.parametric_memory_index import (
        IndexStaleError,
        build_or_refresh_index,
        index_path_for,
        needs_index,
        recall_via_index,
    )
    from agentic_swmm.memory.version_compat import migrate_record

    store_path = Path(store_path)
    if not store_path.is_file():
        return []

    filters = filters or {}

    # SQLite acceleration: only kicks in once the JSONL is large enough
    # that a linear scan starts paying. For small stores the index
    # build itself would dominate the cost so we stay on the linear
    # path. ``needs_index`` answers both "is the store big enough" and
    # "is the existing sidecar fresh".
    try:
        if needs_index(store_path):
            build_or_refresh_index(store_path)
        sidecar = index_path_for(store_path)
        if sidecar.is_file():
            try:
                return recall_via_index(store_path, filters)
            except IndexStaleError:
                # Sidecar is older than the JSONL (e.g. an append
                # raced the read). Fall back to the linear path which
                # is always correct against the canonical JSONL.
                pass
    except (OSError, ValueError):
        # SQLite construction or query failure must never block the
        # caller — fall back to the linear scan.
        pass

    matches: list[dict[str, Any]] = []
    for row in iter_rows(store_path):
        row = migrate_record("parametric_memory", row)
        if _matches(row, filters):
            matches.append(row)
    return matches


def _matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Return ``True`` if ``row`` matches every key/value in ``filters``."""
    for key, expected in filters.items():
        if _resolve(row, key) != expected:
            return False
    return True


def _resolve(row: dict[str, Any], dotted_key: str) -> Any:
    """Resolve ``a.b.c`` against ``row`` — return ``None`` if missing."""
    cursor: Any = row
    for part in dotted_key.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor
