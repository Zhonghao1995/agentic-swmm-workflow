"""Parametric memory: per-run quantitative records (PRD-06 Phase A.1).

A modeler thinks in *parameters and metrics*, not in text patterns.
``lessons_learned.md`` catalogs prose lessons; this module catalogs the
quantitative facts about each SWMM run so the agent can answer "what
Manning's *n* did I use last project" or "what continuity did the LID
intervention produce" without grepping prose.

Backend
-------
JSONL — one line per run. Append-only. Reads stream the file. Indexing
upgrades (SQLite) live behind the same two verbs and are deferred until
the file exceeds ~1k rows in practice (PRD §4.1). At Phase A scale a
linear scan is below the cost of the SWMM run that produced the row.

Schema (``schema_version == "1.0"``)
-----------------------------------
- ``run_id``: stable identifier (matches ``experiment_provenance.json``)
- ``case_name``: human label
- ``swmm_version``: e.g. ``"5.2.4"``; needed for cross-version refusal
  later (Phase C)
- ``model_structure``: routing / infiltration / time_step / duration
- ``qa_metrics``: continuity %, mass-balance % (from .rpt)
- ``performance_metrics``: NSE / KGE / PBIAS (when calibrated)
- ``watershed_classification``: size_km2 / impervious_pct / climate
- ``recorded_utc``: ISO-8601 timestamp

Failure mode
------------
Atomic-append: each call opens with ``"a"`` and writes a single
JSON-encoded line. Concurrent readers may observe a torn final line but
earlier records are intact. This mirrors the
``llm_calls.jsonl`` contract elsewhere in the audit pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"


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

    Raises:
        ValueError: ``run_id`` or ``case_name`` is empty. Both are
            required for downstream recall (joins with provenance,
            display in the audit summary).
    """
    if not record.run_id or not record.run_id.strip():
        raise ValueError("ParametricRecord.run_id must be a non-empty string")
    if not record.case_name or not record.case_name.strip():
        raise ValueError("ParametricRecord.case_name must be a non-empty string")

    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.to_dict()
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with store_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def recall_parametric(
    store_path: Path, filters: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Return all rows from ``store_path`` matching ``filters``.

    ``filters`` keys may use dotted notation to address nested fields
    (``"qa_metrics.runoff_continuity_pct"``). Values match by equality
    today; range queries are a Phase B extension. A missing or empty
    filter dict returns all rows.

    Missing files yield ``[]`` (not an error) so first-time callers
    do not have to special-case a fresh project.
    """
    from agentic_swmm.memory.version_compat import migrate_record

    store_path = Path(store_path)
    if not store_path.is_file():
        return []

    filters = filters or {}
    matches: list[dict[str, Any]] = []
    with store_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                # Torn final line during a concurrent write — skip.
                continue
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
