"""Calibration memory: per-calibration goodness-of-fit records (PRD-06 Phase B.3).

The modeler runs SCE-UA / DREAM-ZS calibrations that produce one
"accepted parameter set" with goodness-of-fit metrics. Today those
outcomes live only in the per-run audit dir. This module persists the
record in a JSONL store parallel to ``parametric_memory.jsonl`` so the
agent can answer "what was the best Manning's *n* for saanich,
use_case=stormwater_event, in the last 6 months" with one append-only
file and a linear scan.

Backend
-------
JSONL — one line per accepted calibration. Same contract as
``parametric_memory.py``: atomic single-line writes, tolerant of torn
final line on read, missing file yields ``[]``. The two stores live
side-by-side under ``memory/modeling-memory/`` so a future indexing
upgrade (SQLite) can subsume both behind the same verbs.

Schema (``SCHEMA_VERSION == "1.0"``)
------------------------------------
- ``run_id`` / ``case_name``: stable join keys with provenance
- ``use_case``: free-form intent label (e.g. ``"stormwater_event"``)
- ``algorithm``: ``"sceua"``, ``"dream_zs"``, or whatever the caller
  used; we do not enumerate it because new methods land outside this
  module
- ``parameters``: ``dict[str, float]`` — the accepted parameter set
- ``objective_name`` / ``objective_value``: primary fit metric, e.g.
  ``"NSE": 0.78``
- ``secondary_metrics``: PBIAS, RMSE, KGE decomposition, etc.
- ``swmm5_version``: needed when cross-version transfer becomes
  meaningful (Phase C)
- ``n_evaluations`` / ``wall_time_s``: rough cost — lets the agent
  estimate budget for follow-up calibrations
- ``created_at``: ISO 8601 timestamp; UTC if you let the writer set
  it

Soft-fail contract
------------------
Empty ``run_id`` or ``case_name`` raises ``ValueError`` — same as
``parametric_memory``. The audit-hook integration that bridges to this
store catches the error so a half-populated provenance never blocks
the rest of the memory pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class CalibrationRecord:
    """One row of calibration memory.

    Mirrors :class:`ParametricRecord` in spirit but carries the
    calibration-specific payload. Frozen so callers can pass the same
    record through multiple helpers without aliasing. Optional fields
    default to empty/None so the writer never barfs on a partial
    calibration (e.g. ``wall_time_s`` not reported by older runs).
    """

    run_id: str
    case_name: str
    use_case: str | None = None
    algorithm: str | None = None
    parameters: dict[str, float] = field(default_factory=dict)
    objective_name: str | None = None
    objective_value: float | None = None
    secondary_metrics: dict[str, float] = field(default_factory=dict)
    swmm5_version: str | None = None
    n_evaluations: int | None = None
    wall_time_s: float | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-versioned dict written to disk."""
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "case_name": self.case_name,
            "use_case": self.use_case,
            "algorithm": self.algorithm,
            "parameters": dict(self.parameters),
            "objective_name": self.objective_name,
            "objective_value": self.objective_value,
            "secondary_metrics": dict(self.secondary_metrics),
            "swmm5_version": self.swmm5_version,
            "n_evaluations": self.n_evaluations,
            "wall_time_s": self.wall_time_s,
            "created_at": self.created_at
            or datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
        }
        return payload


def record_calibration_run(store_path: Path, record: CalibrationRecord) -> None:
    """Append ``record`` to the JSONL store at ``store_path``.

    Creates parent directories if needed. The write is a single
    ``open(..., "a")`` call so concurrent readers always see complete
    earlier lines.

    Raises:
        ValueError: ``run_id`` or ``case_name`` is empty. Both are
            required for downstream recall (the JSONL is joined back
            to ``experiment_provenance.json``).
    """
    if not record.run_id or not record.run_id.strip():
        raise ValueError("CalibrationRecord.run_id must be a non-empty string")
    if not record.case_name or not record.case_name.strip():
        raise ValueError("CalibrationRecord.case_name must be a non-empty string")

    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.to_dict()
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with store_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def recall_calibration(
    store_path: Path, filters: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Return all rows from ``store_path`` matching ``filters``.

    ``filters`` keys may use dotted notation to address nested fields:
    ``"parameters.manning_n"`` resolves to ``row["parameters"]["manning_n"]``
    and ``"secondary_metrics.pbias"`` resolves to
    ``row["secondary_metrics"]["pbias"]``. Values match by equality;
    range queries are a Phase C extension. Missing or empty ``filters``
    returns all rows.

    Missing files yield ``[]`` (not an error) so first-time callers
    do not need to special-case a fresh project.
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
            row = migrate_record("calibration_memory", row)
            if _matches(row, filters):
                matches.append(row)
    return matches


def _matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Return ``True`` when ``row`` matches every key/value in ``filters``."""
    for key, expected in filters.items():
        if _resolve(row, key) != expected:
            return False
    return True


def _resolve(row: dict[str, Any], dotted_key: str) -> Any:
    """Resolve ``a.b.c`` against ``row`` — return ``None`` when missing."""
    cursor: Any = row
    for part in dotted_key.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor
