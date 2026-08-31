"""Pure threshold evaluator for the HITL pause subsystem (PRD-Z).

The evaluator turns a QA report dict + a thresholds dict into a list of
:class:`ThresholdHit` records. Thresholds are loaded from
``docs/hitl-thresholds.md`` via :func:`load_thresholds_from_md` — the
YAML front-matter is the source of truth, the prose below it documents
the rationale the human hydrologist writes per threshold.

The PRD's testing decisions enumerate the patterns this module must
recognise:

* ``continuity_error_over_threshold``
* ``peak_flow_deviation_over_threshold``
* ``pour_point_suspect``
* ``calibration_nse_low``

Each ``threshold`` row carries a ``measured_key`` that points into the
QA report via dotted lookup, an ``operator`` (``>``, ``<``, ``>=``,
``<=``, ``==``, ``!=``) and a ``value``. The evaluator is otherwise
agnostic to the pattern name — new patterns can be added by appending
rows to the markdown file. This module intentionally does no I/O once
``evaluate`` is called: ``load_thresholds_from_md`` reads the file
once, and the resulting dict is the contract.
"""

from __future__ import annotations

import operator as _operator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

from agentic_swmm.hitl.banding import Bands, grade


_PLACEHOLDER_RATIONALE_MARKER = "HYDROLOGY-TODO"


@dataclass(frozen=True)
class ThresholdHit:
    """One QA threshold breach, ready to be surfaced to a human reviewer."""

    pattern: str
    severity: Literal["warn", "block"]
    measured_value: Any
    threshold_value: Any
    evidence_ref: str
    message: str
    rationale_is_placeholder: bool = False
    # Graded fields (spec fuzzy-hitl-gates); ``None`` on crisp entries.
    level: str | None = None
    memberships: dict[str, float] | None = None
    bands: dict[str, float] | None = None
    direction: str | None = None


_OPERATORS = {
    ">": _operator.gt,
    "<": _operator.lt,
    ">=": _operator.ge,
    "<=": _operator.le,
    "==": _operator.eq,
    "!=": _operator.ne,
}


def _dotted_lookup(report: dict[str, Any], key: str) -> Any:
    """Walk a dotted ``a.b.c`` path through nested dicts.

    Returns ``None`` if any segment is missing or not a dict. The
    evaluator treats a missing key as "no hit" — a partially-populated
    QA report should never crash the threshold pass.
    """
    cursor: Any = report
    for part in key.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def _is_placeholder_rationale(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return _PLACEHOLDER_RATIONALE_MARKER in value


def evaluate(qa_report: dict, thresholds: dict) -> list[ThresholdHit]:
    """Return the list of :class:`ThresholdHit` for ``qa_report``.

    ``qa_report`` is any nested dict whose leaves are comparable values
    (numbers, bools, strings). ``thresholds`` is the dict produced by
    :func:`load_thresholds_from_md` (or constructed in tests). The
    function is pure and side-effect free.
    """
    if not isinstance(qa_report, dict) or not isinstance(thresholds, dict):
        return []
    hits: list[ThresholdHit] = []
    for pattern, spec in thresholds.items():
        if not isinstance(spec, dict):
            continue
        measured_key = spec.get("measured_key")
        operator_symbol = spec.get("operator")
        threshold_value = spec.get("value")
        if not measured_key or operator_symbol not in _OPERATORS:
            continue
        compare = _OPERATORS[operator_symbol]
        measured = _dotted_lookup(qa_report, str(measured_key))
        if measured is None:
            continue
        # Graded path (spec fuzzy-hitl-gates): a bands block replaces
        # the crisp comparison for numeric values. Malformed bands
        # parse to None and fall through to the crisp path, so a
        # misconfigured entry can weaken nothing.
        graded_bands = Bands.from_spec(spec)
        if (
            graded_bands is not None
            and isinstance(measured, (int, float))
            and not isinstance(measured, bool)
        ):
            band_memberships, band_level = grade(float(measured), graded_bands)
            if band_level == "low":
                continue
            hits.append(
                ThresholdHit(
                    pattern=str(pattern),
                    severity="warn" if band_level == "medium" else "block",
                    measured_value=measured,
                    threshold_value=threshold_value,
                    evidence_ref=str(spec.get("evidence_path", "")),
                    message=str(spec.get("message", "")),
                    rationale_is_placeholder=_is_placeholder_rationale(spec.get("rationale")),
                    level=band_level,
                    memberships=band_memberships,
                    bands={
                        "fine": graded_bands.fine,
                        "centre": graded_bands.centre,
                        "bad": graded_bands.bad,
                    },
                    direction=graded_bands.direction,
                )
            )
            continue
        try:
            triggered = bool(compare(measured, threshold_value))
        except TypeError:
            # Type-mismatched comparisons (e.g., comparing None to a
            # number) are silently ignored so a malformed QA report
            # cannot crash the evaluator. They are also not a "hit".
            continue
        if not triggered:
            continue
        hits.append(
            ThresholdHit(
                pattern=str(pattern),
                severity=_coerce_severity(spec.get("severity")),
                measured_value=measured,
                threshold_value=threshold_value,
                evidence_ref=str(spec.get("evidence_path", "")),
                message=str(spec.get("message", "")),
                rationale_is_placeholder=_is_placeholder_rationale(spec.get("rationale")),
            )
        )
    return hits


def _coerce_severity(value: Any) -> Literal["warn", "block"]:
    if isinstance(value, str) and value.strip().lower() == "warn":
        return "warn"
    return "block"


def load_thresholds_from_md(path: Path) -> dict:
    """Load the YAML front-matter from ``docs/hitl-thresholds.md``.

    The front-matter is delimited by ``---`` lines. The first
    front-matter block in the file is parsed; everything else (prose,
    rationale paragraphs, examples) is ignored. The function returns the
    ``thresholds`` dict that lives inside the front-matter so callers
    can pass it directly to :func:`evaluate`.

    Raises ``ValueError`` if the file has no YAML front-matter or no
    ``thresholds`` key. This is louder than a silent empty dict because
    a misconfigured thresholds file is a serious operational hazard.
    """
    text = path.read_text(encoding="utf-8")
    front = _extract_front_matter(text)
    if front is None:
        raise ValueError(
            f"{path}: no YAML front-matter found "
            "(expected --- delimited block at top of file)"
        )
    try:
        data = yaml.safe_load(front)
    except yaml.YAMLError as exc:
        # The audit seam catches ValueError; a leaking yaml error would
        # crash `aiswmm audit` on a hand-edit typo in the doc.
        raise ValueError(f"{path}: malformed YAML front-matter: {exc}") from exc
    if not isinstance(data, dict):
        data = {}
    thresholds = data.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError(
            f"{path}: YAML front-matter does not define a 'thresholds' "
            "mapping (got {type(thresholds).__name__!s})"
        )
    return thresholds


def _extract_front_matter(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    body: list[str] = []
    for raw in lines[1:]:
        if raw.strip() == "---":
            return "\n".join(body)
        body.append(raw)
    return None


def patterns(thresholds: dict) -> Iterable[str]:
    """Return the pattern names defined in ``thresholds``.

    Convenience used by call-sites that want to render an "available
    patterns" hint without hard-coding the four PRD names. Kept here so
    the loader and the introspection helper move together.
    """
    return list(thresholds.keys()) if isinstance(thresholds, dict) else []
