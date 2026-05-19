"""Case-adaptive threshold proposals from calibration memory (PRD-07 Phase 4).

Suppose a watershed has been calibrated five times — each time the
accepted parameter set sat well under 0.5% runoff continuity error.
A static 5% WARN threshold (the SWMM User Manual default) is then
loose for *this case*: a run that posts 2.5% continuity is anomalous
relative to history but classified ``PASS`` against the library.

This module produces an **advisory proposal**: looking at the
:mod:`calibration_memory` JSONL store, it returns a suggested
``{"warn": ..., "fail": ...}`` dict the caller can choose to apply.
The runtime gate stays the library default until the caller decides
otherwise; the proposal is just a hint.

Why advisory only
-----------------
- Calibration history is small (typically 1-10 rows). A silent
  tightening based on five samples would be too aggressive.
- The maintainer should see *why* the runtime considered tightening
  the gate. The :func:`propose_case_threshold` function returns a
  ``rationale`` string the caller can log to the memory trace
  (confidence label ``"memory_informed"``).
- Phase 5 will wire this proposal into a HITL gate. Phase 4 builds
  only the proposal mechanism so the wire-up has a typed input.

The proposal's safety floor: never tighten below 10% of the library
default. SWMM continuity is reported to three decimals so anything
tighter than 0.1% is noise.
"""

from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any

from agentic_swmm.memory.calibration_memory import recall_calibration


# Minimum number of historical calibration runs needed to make any
# proposal. Below this we return the default unchanged — five samples
# is the smallest count where a median is more than a single outlier.
MIN_HISTORICAL_SAMPLES = 5


# Floor on how aggressively the proposal can tighten the library default.
# A proposal of "warn at 10% of default" is the most aggressive shift
# we ever generate; anything tighter is reported as noise-floor and
# the default is kept.
MIN_TIGHTEN_FACTOR = 0.1


def _extract_metric_value(row: dict[str, Any], metric: str) -> float | None:
    """Pull the metric value from a calibration_memory row.

    The metric may live under ``secondary_metrics.<name>`` (the
    calibration writer's primary location) or, for forward-compat,
    directly on the row. Returns ``None`` when the metric is absent
    or the value is non-numeric.
    """
    candidates: list[Any] = []
    secondary = row.get("secondary_metrics")
    if isinstance(secondary, dict) and metric in secondary:
        candidates.append(secondary[metric])
    if metric in row:
        candidates.append(row[metric])

    for raw in candidates:
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _historical_metric_values(
    *, case_name: str, use_case: str, metric: str, calibration_store: Path
) -> list[float]:
    """Return all historical numeric values of ``metric`` for the case.

    The filter is ``case_name + use_case + (metric present)`` — exactly
    the slice the runtime will use to decide whether to propose
    tightening.
    """
    filters = {"case_name": case_name, "use_case": use_case}
    rows = recall_calibration(Path(calibration_store), filters)
    out: list[float] = []
    for row in rows:
        value = _extract_metric_value(row, metric)
        if value is None:
            continue
        out.append(abs(value))  # magnitude — same convention as classify_metric
    return out


def _safe_floor(default_value: float) -> float:
    """Smallest threshold we will ever propose."""
    return default_value * MIN_TIGHTEN_FACTOR


def propose_case_threshold(
    case_name: str,
    use_case: str,
    metric: str,
    *,
    calibration_store: Path,
    default_thresholds: dict[str, Any],
) -> dict[str, Any]:
    """Return a proposed ``{"warn", "fail", "rationale", "n_historical"}`` dict.

    Behaviour:

    - With fewer than :data:`MIN_HISTORICAL_SAMPLES` historical rows,
      the function returns ``default_thresholds`` *unchanged* with a
      rationale explaining the abstention. The caller observes the
      same gate they would have used without consulting memory.
    - With ≥ :data:`MIN_HISTORICAL_SAMPLES` rows, the proposal is
      ``warn = max(2 * median, floor)`` and ``fail = library fail`` —
      tightening WARN only. FAIL stays library-default so a regression
      that breaches the manual's failure band is still surfaced even
      if the case's median sits well under it.

    Arguments:
        case_name: Watershed / project identifier — exact match against
            ``calibration_memory.jsonl`` rows.
        use_case: Secondary filter (e.g. ``"stormwater_event"``).
        metric: Metric to propose for; typically
            ``"runoff_continuity_pct"`` or ``"flow_continuity_pct"``.
        calibration_store: Path to ``calibration_memory.jsonl``.
        default_thresholds: The library / overlay-resolved gate the
            caller would have used absent memory. Must contain
            ``warn`` and ``fail`` keys.

    Returns:
        Dict with keys ``warn``, ``fail``, ``rationale``, and
        ``n_historical``. ``rationale`` is always non-empty so the
        caller has something to write into :func:`log_memory_decision`.
    """
    default_warn = default_thresholds.get("warn")
    default_fail = default_thresholds.get("fail")

    history = _historical_metric_values(
        case_name=case_name,
        use_case=use_case,
        metric=metric,
        calibration_store=calibration_store,
    )
    n_historical = len(history)

    if n_historical < MIN_HISTORICAL_SAMPLES:
        rationale = (
            f"insufficient calibration history for "
            f"{case_name}/{use_case}/{metric}: {n_historical} "
            f"sample(s) < {MIN_HISTORICAL_SAMPLES} minimum — keeping default"
        )
        return {
            "warn": default_warn,
            "fail": default_fail,
            "rationale": rationale,
            "n_historical": n_historical,
        }

    if default_warn is None:
        rationale = (
            f"default warn threshold is null for {metric}; cannot "
            f"propose tightening without a numeric baseline"
        )
        return {
            "warn": default_warn,
            "fail": default_fail,
            "rationale": rationale,
            "n_historical": n_historical,
        }

    historical_median = median(history)
    proposed_warn = max(historical_median * 2.0, _safe_floor(float(default_warn)))

    # If history is already at or above the default, do not loosen — the
    # proposal mechanism only tightens.
    if proposed_warn >= float(default_warn):
        rationale = (
            f"historical median {historical_median:.3f} for {metric} on "
            f"{case_name}/{use_case} is not tighter than default warn "
            f"{default_warn} — keeping default"
        )
        return {
            "warn": default_warn,
            "fail": default_fail,
            "rationale": rationale,
            "n_historical": n_historical,
        }

    rationale = (
        f"{n_historical} prior calibration(s) for {case_name}/{use_case} "
        f"show median |{metric}| = {historical_median:.3f}; proposing "
        f"warn={proposed_warn:.3f} (2x median, floor {_safe_floor(float(default_warn)):.3f}) "
        f"vs default warn={default_warn}; fail held at default {default_fail}"
    )
    return {
        "warn": proposed_warn,
        "fail": default_fail,
        "rationale": rationale,
        "n_historical": n_historical,
    }


__all__ = [
    "MIN_HISTORICAL_SAMPLES",
    "MIN_TIGHTEN_FACTOR",
    "propose_case_threshold",
]
