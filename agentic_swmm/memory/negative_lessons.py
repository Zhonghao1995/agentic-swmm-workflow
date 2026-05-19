"""Negative lessons: parameter regions that consistently fail (PRD-06 Phase C.2).

Why this module exists
----------------------
``parametric_memory`` and ``calibration_memory`` record *what worked*.
Equally important — and absent today — is what *failed*: the runs that
posted FAIL continuity, the calibrations that diverged, the parameter
sets that are physically out of band. Without that record the agent
will happily re-propose a known-bad parameter set the next time the
case looks similar.

Backend
-------
JSONL — one line per recorded failure. Same contract as the other
memory stores: atomic append, tolerant of torn final line on read,
missing file yields ``[]``. The store lives next to ``parametric_memory.jsonl``
under ``memory/modeling-memory/`` so a future indexing upgrade can
subsume all three behind the same verbs.

Schema (``SCHEMA_VERSION == "1.0"``)
------------------------------------
- ``run_id`` / ``case_name``: stable join keys with provenance
- ``lesson_type``: enumerated — ``"continuity_fail" | "calibration_diverged" | "non_physical_param"``
- ``parameters_tried``: ``dict[str, float]`` — the parameter set the run used
- ``metric_observed``: ``dict[str, float]`` — what came back (e.g. ``{"runoff_continuity_pct": 12.4}``)
- ``note``: free-form analyst remark; populated by the audit hook with the failure code
- ``recorded_at``: ISO 8601; UTC if the writer auto-fills

The ``is_param_set_known_bad`` helper closes the loop: given a
candidate parameter set, return any prior negative lesson whose
parameters are within ``tolerance_pct`` percent of every key. The
default ``5.0%`` is loose enough that small jitter on Manning's *n*
still hits a known-bad region but tight enough that genuinely new
proposals slip through.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"

# Enumerated lesson types. Kept as a frozenset so misuse fails fast at
# write time rather than producing un-recallable rows.
_LESSON_TYPES = frozenset(
    {"continuity_fail", "calibration_diverged", "non_physical_param"}
)


@dataclass(frozen=True)
class NegativeLesson:
    """One row of negative-lessons memory.

    Frozen so callers can pass the same lesson through multiple helpers
    without aliasing. ``parameters_tried`` and ``metric_observed``
    default to empty dicts so the writer never barfs on a partially
    populated failure (some runs FAIL before metrics are recorded).
    """

    run_id: str
    case_name: str
    lesson_type: str
    parameters_tried: dict[str, float] = field(default_factory=dict)
    metric_observed: dict[str, float] = field(default_factory=dict)
    note: str = ""
    recorded_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-versioned dict written to disk."""
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "case_name": self.case_name,
            "lesson_type": self.lesson_type,
            "parameters_tried": dict(self.parameters_tried),
            "metric_observed": dict(self.metric_observed),
            "note": self.note,
            "recorded_at": self.recorded_at
            or datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        }


def record_negative_lesson(store: Path, lesson: NegativeLesson) -> None:
    """Append ``lesson`` to the JSONL store at ``store``.

    Validates ``run_id``, ``case_name``, and ``lesson_type`` so a row
    that no recall verb will ever match never lands on disk.

    Raises:
        ValueError: ``run_id`` or ``case_name`` is empty, or
            ``lesson_type`` is not in the enumerated set.
    """
    if not lesson.run_id or not lesson.run_id.strip():
        raise ValueError("NegativeLesson.run_id must be a non-empty string")
    if not lesson.case_name or not lesson.case_name.strip():
        raise ValueError("NegativeLesson.case_name must be a non-empty string")
    if lesson.lesson_type not in _LESSON_TYPES:
        raise ValueError(
            f"NegativeLesson.lesson_type must be one of {sorted(_LESSON_TYPES)}"
        )

    store = Path(store)
    store.parent.mkdir(parents=True, exist_ok=True)
    payload = lesson.to_dict()
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with store.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def recall_negative_lessons(
    store: Path, filters: dict[str, Any] | None = None
) -> list[NegativeLesson]:
    """Return all rows from ``store`` matching ``filters``.

    Same dotted-key contract as the other memory stores: keys may
    address nested fields (``"parameters_tried.manning_n"``). Values
    match by equality. Missing files yield ``[]`` (not an error).

    Returns :class:`NegativeLesson` dataclasses rather than raw dicts
    so the caller can use attribute access without each site doing the
    same dict -> dataclass dance.
    """
    from agentic_swmm.memory.version_compat import migrate_record

    store = Path(store)
    if not store.is_file():
        return []

    filters = filters or {}
    matches: list[NegativeLesson] = []
    with store.open("r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                # Torn final line during a concurrent write — skip.
                continue
            row = migrate_record("negative_lessons", row)
            if _matches(row, filters):
                matches.append(_row_to_lesson(row))
    return matches


def is_param_set_known_bad(
    store: Path,
    case_name: str,
    params: dict[str, float],
    *,
    tolerance_pct: float = 5.0,
) -> NegativeLesson | None:
    """Return any negative lesson whose parameters are within ``tolerance_pct``.

    A lesson "matches" when ``params`` covers every key in the lesson's
    ``parameters_tried`` *and* every covered value is within
    ``tolerance_pct`` percent of the lesson's value (relative to the
    lesson value's magnitude). Missing keys in the candidate set mean
    "we don't know if this regions is bad" — we err on the side of
    *not* flagging false positives, so the lesson is skipped.

    Returns the first matching lesson (insertion order) or ``None`` if
    nothing matches. Tolerance is computed as
    ``abs(candidate - recorded) / max(abs(recorded), 1e-9) * 100`` so
    zero-valued recorded parameters still work without div-by-zero.

    Round 7: when the markdown store
    (``negative_lessons.md`` next to the given JSONL store) exists, the
    markdown lookup wins. The JSONL path stays as the back-compat
    fallback for projects that have not yet run the one-shot migration.
    """
    if tolerance_pct < 0:
        raise ValueError("tolerance_pct must be non-negative")

    md_store = Path(store).with_suffix(".md")
    if md_store.is_file():
        from agentic_swmm.memory.negative_lessons_markdown import (
            is_param_set_known_bad_md,
        )

        md_match = is_param_set_known_bad_md(
            md_store,
            case_name,
            params,
            tolerance_pct=tolerance_pct,
        )
        if md_match is None:
            return None
        # Project the markdown record into the legacy NegativeLesson
        # shape so existing callers keep their attribute access.
        return NegativeLesson(
            run_id=md_match.evidence_runs[0] if md_match.evidence_runs else "",
            case_name=md_match.case,
            lesson_type=md_match.lesson_type,
            parameters_tried=dict(md_match.parameters_tried),
            metric_observed={},
            note=md_match.note,
            recorded_at=md_match.last_seen,
        )

    lessons = recall_negative_lessons(store, {"case_name": case_name})
    if not lessons:
        return None

    for lesson in lessons:
        recorded = lesson.parameters_tried
        if not recorded:
            continue
        # Require the candidate set to cover every recorded key so we
        # never flag a partial match as "known bad".
        if not all(key in params for key in recorded):
            continue
        within = True
        for key, recorded_value in recorded.items():
            candidate = params[key]
            denom = max(abs(float(recorded_value)), 1e-9)
            pct = abs(float(candidate) - float(recorded_value)) / denom * 100.0
            if pct > tolerance_pct:
                within = False
                break
        if within:
            return lesson
    return None


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


def _row_to_lesson(row: dict[str, Any]) -> NegativeLesson:
    """Hydrate a JSON row back into a :class:`NegativeLesson`."""
    return NegativeLesson(
        run_id=str(row.get("run_id", "")),
        case_name=str(row.get("case_name", "")),
        lesson_type=str(row.get("lesson_type", "")),
        parameters_tried=dict(row.get("parameters_tried") or {}),
        metric_observed=dict(row.get("metric_observed") or {}),
        note=str(row.get("note", "")),
        recorded_at=row.get("recorded_at"),
    )
