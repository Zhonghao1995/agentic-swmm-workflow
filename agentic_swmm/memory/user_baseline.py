"""User-baseline percentile helper for postflight QA (Round 6).

PRD-07 Phase 4 specifies that, when a user has accumulated enough
historical observations of a given metric on a given case + use_case,
the runtime should classify new runs against *their own* distribution
instead of (or alongside) the library three-tier thresholds. PR #152
shipped the advisory ``propose_case_threshold`` helper but did not
apply it. This module is the binding helper: it walks the parametric
memory JSONL, applies case + use_case + recency filters, extracts the
metric via a dotted path, and returns a :class:`UserBaseline` carrying
p50/p75/p95/p99 + mean/std.

Why stdlib ``statistics`` (no numpy)
------------------------------------
The whole compute is bounded by the number of historical rows the
user has accumulated — at Round 6 scale that is tens of rows, not
millions. ``statistics.quantiles`` is fully sufficient and keeps the
import surface flat for callers (e.g. ``postflight_qa``) that today
have no numpy dependency.

Failure mode
------------
Every read path is exception-safe at the boundary. Missing file,
torn lines, missing metric on a given row — each yields "no
contribution" rather than an exception. The function returns
``None`` whenever the surviving sample is below
``min_observations`` so the caller has a clean signal to fall back
to the library thresholds.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UserBaseline:
    """Per-user (case + use_case + metric) historical distribution.

    Frozen because callers treat the baseline as evidence — once
    computed for a postflight comparison, downstream code should
    not be able to scribble on the percentiles.
    """

    case_name: str
    use_case: str
    metric_path: str
    n_observations: int
    p50: float
    p75: float
    p95: float
    p99: float
    mean: float
    std: float
    sources: list[str] = field(default_factory=list)


def _resolve_dotted(row: dict[str, Any], dotted: str) -> Any:
    """Walk ``a.b.c`` over a JSONL row; ``None`` when missing."""
    cursor: Any = row
    for part in dotted.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def _row_matches_case_and_use_case(
    row: dict[str, Any], *, case_name: str, use_case: str
) -> bool:
    if row.get("case_name") != case_name:
        return False
    structure = row.get("model_structure") or {}
    if not isinstance(structure, dict):
        return False
    return structure.get("use_case") == use_case


def _within_lookback(
    row: dict[str, Any], *, cutoff_iso: str | None
) -> bool:
    """Return True when the row's recorded_utc is on or after ``cutoff_iso``.

    Rows without a parsable timestamp pass through (the historical
    record is otherwise valid; the user just didn't stamp it). This is
    deliberately permissive — the lookback filter is an optional
    refinement, not a correctness gate.
    """
    if cutoff_iso is None:
        return True
    ts = row.get("recorded_utc")
    if not isinstance(ts, str) or not ts:
        return True
    # ISO 8601 strings sort lexicographically when they share offset
    # ("Z"); we accept the cheap comparison and only fall back to
    # parsing when the strings have different shapes.
    if ts >= cutoff_iso:
        return True
    try:
        # Some writers may have used "+00:00" instead of "Z".
        normalised = ts.replace("Z", "+00:00")
        cutoff_normalised = cutoff_iso.replace("Z", "+00:00")
        return datetime.fromisoformat(normalised) >= datetime.fromisoformat(
            cutoff_normalised
        )
    except ValueError:
        return True


def _percentile(values: list[float], pct: float) -> float:
    """Return the ``pct``-th percentile via stdlib ``statistics.quantiles``.

    Uses ``n=100`` with the exclusive method so a stable percentile is
    available for any 1..99 split. For < 2 samples we collapse to the
    lone value; for a constant-value sample we return the value. Both
    branches avoid the stdlib's ``StatisticsError`` so the caller never
    has to special-case low-sample paths.
    """
    if not values:
        return 0.0
    if len(values) < 2:
        return float(values[0])
    try:
        quantiles = statistics.quantiles(values, n=100, method="exclusive")
    except statistics.StatisticsError:
        return float(max(values))
    # ``quantiles`` returns 99 cut points (between bins 1..99). The
    # p-th percentile is at index p-1.
    idx = max(0, min(98, int(round(pct)) - 1))
    return float(quantiles[idx])


def compute_user_baseline(
    parametric_store: Path,
    *,
    case_name: str,
    use_case: str,
    metric_path: str,
    lookback_days: int | None = None,
    min_observations: int = 5,
) -> UserBaseline | None:
    """Compute the user's historical distribution for one metric.

    Arguments:
        parametric_store: Filesystem path to the JSONL store, typically
            ``memory/modeling-memory/parametric_memory.jsonl``. Missing
            files yield ``None``.
        case_name: Filter — only rows whose ``case_name`` matches
            contribute. Must match the writer's value exactly; this is
            *not* a watershed-similarity helper.
        use_case: Filter — only rows whose
            ``model_structure.use_case`` matches contribute.
        metric_path: Dotted JSON path into the row, e.g.
            ``"qa_metrics.runoff_continuity_pct"``.
        lookback_days: When set, drop rows older than this many days
            relative to the function's wall clock. Missing or
            unparsable timestamps pass through.
        min_observations: Minimum surviving sample size required for
            the baseline to be returned. Below this we yield ``None``
            so the caller falls back to library thresholds.

    Returns:
        A :class:`UserBaseline` when the sample clears
        ``min_observations``; otherwise ``None``. The function
        is exception-safe at the boundary.
    """
    store = Path(parametric_store)
    if not store.is_file():
        return None

    cutoff_iso: str | None = None
    if lookback_days is not None and lookback_days > 0:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(
            days=int(lookback_days)
        )
        cutoff_iso = (
            cutoff_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
        )

    values: list[float] = []
    sources: list[str] = []
    try:
        with store.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    # Torn line — skip silently.
                    continue
                if not isinstance(row, dict):
                    continue
                if not _row_matches_case_and_use_case(
                    row, case_name=case_name, use_case=use_case
                ):
                    continue
                if not _within_lookback(row, cutoff_iso=cutoff_iso):
                    continue
                raw_metric = _resolve_dotted(row, metric_path)
                if raw_metric is None:
                    continue
                try:
                    value = float(raw_metric)
                except (TypeError, ValueError):
                    continue
                values.append(abs(value))
                run_id = row.get("run_id")
                if isinstance(run_id, str) and run_id:
                    sources.append(run_id)
    except OSError:
        return None

    if len(values) < int(min_observations):
        return None

    sorted_values = sorted(values)
    p50 = _percentile(sorted_values, 50)
    p75 = _percentile(sorted_values, 75)
    p95 = _percentile(sorted_values, 95)
    p99 = _percentile(sorted_values, 99)
    mean = statistics.fmean(sorted_values)
    std = (
        statistics.pstdev(sorted_values) if len(sorted_values) > 1 else 0.0
    )

    return UserBaseline(
        case_name=case_name,
        use_case=use_case,
        metric_path=metric_path,
        n_observations=len(sorted_values),
        p50=p50,
        p75=p75,
        p95=p95,
        p99=p99,
        mean=float(mean),
        std=float(std),
        sources=sources,
    )


__all__ = [
    "UserBaseline",
    "compute_user_baseline",
]
