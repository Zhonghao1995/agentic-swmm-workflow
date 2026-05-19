"""Watershed similarity scoring (PRD-06 Phase C.1).

Why this module exists
----------------------
``parametric_memory`` and ``calibration_memory`` carry per-run records
keyed by ``case_name``. When the runtime picks a row to transfer
parameters from, exact case match is not always available — most
projects calibrate on a sibling watershed first. This module scores
how *similar* two watersheds are so the runtime can pick the most
relevant case to borrow from.

What "similar" means here
-------------------------
A small, hand-tunable feature vector: area (log-space because
watershed areas span orders of magnitude), imperviousness, mean slope,
and structural size (``log(n_subcatchments)``, ``log(n_conduits)``).
We deliberately stop short of richer features (land-use vectors,
soil-class fractions) — those land when the calibration store
carries enough rows to make them pay back. The score is a normalized
weighted L2 distance squashed to ``[0, 1]`` via ``1 / (1 + d)``.

What we read from an INP
------------------------
The minimal section-aware reader pulls block sizes (``[SUBCATCHMENTS]``,
``[CONDUITS]``, ``[OUTFALLS]``) and computes area-weighted means of
``%Imperv`` and ``%Slope`` from the SUBCATCHMENTS rows. SWMM's INP
format is whitespace-tolerant and column-headed by ``;``-prefixed
comment rows; we follow the same convention as ``preflight.py`` —
no third-party parser, just regex + section split.

Failure modes
-------------
Missing sections, odd whitespace, truncated rows: the reader tolerates
all of these and returns whatever it could compute. Counts default to
``0``; means default to ``0.0`` when no rows are usable. The caller
decides whether a watershed with zero subcatchments is comparable.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path


_SECTION_RE = re.compile(r"^\s*\[([A-Z_]+)\]\s*$")


@dataclass(frozen=True)
class WatershedAttributes:
    """Minimal feature vector for watershed-to-watershed similarity.

    Only the fields the scorer reads. ``dominant_landuse`` is optional
    metadata we record when the INP carries enough information to
    classify (today: never — the field is here so future readers can
    populate it without changing the public surface).
    """

    area_ha: float
    imperv_pct: float
    mean_slope_pct: float
    n_subcatchments: int
    n_conduits: int
    n_outfalls: int
    dominant_landuse: str | None = None


def _parse_sections(text: str) -> dict[str, list[str]]:
    """Split INP text into ``{section: [non-comment data lines]}``.

    Mirrors the helper in ``preflight.py`` so behaviour stays
    consistent across the runtime: blank lines and ``;`` comments are
    stripped, headers are upper-cased.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(";"):
            continue
        m = _SECTION_RE.match(raw)
        if m:
            current = m.group(1).upper()
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        sections[current].append(stripped)
    return sections


def _safe_float(token: str) -> float | None:
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


def extract_attributes_from_inp(inp_path: Path) -> WatershedAttributes:
    """Read ``inp_path`` and return a :class:`WatershedAttributes`.

    SUBCATCHMENTS columns (per SWMM 5 manual):
    ``Name RainGage Outlet Area %Imperv Width %Slope CurbLen [SnowPack]``.
    We pull ``Area`` (col 3), ``%Imperv`` (col 4), and ``%Slope`` (col 6)
    when each row has enough tokens. Imperv and slope are area-weighted
    over all SUBCATCHMENTS rows so a watershed dominated by one big
    impervious subcatchment scores as such.

    Missing sections / missing tokens are silently tolerated — the
    record carries whatever the reader could extract, and zero-row
    defaults bubble up to a score the caller can decide about.
    """
    inp_path = Path(inp_path)
    try:
        text = inp_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    sections = _parse_sections(text)

    sub_rows = sections.get("SUBCATCHMENTS", [])
    n_subcatchments = len(sub_rows)
    total_area = 0.0
    weighted_imperv = 0.0
    weighted_slope = 0.0
    weight_imperv = 0.0
    weight_slope = 0.0
    for row in sub_rows:
        cols = row.split()
        if len(cols) < 4:
            continue
        area = _safe_float(cols[3])
        if area is None or area <= 0:
            continue
        total_area += area
        if len(cols) >= 5:
            imperv = _safe_float(cols[4])
            if imperv is not None:
                weighted_imperv += imperv * area
                weight_imperv += area
        if len(cols) >= 7:
            slope = _safe_float(cols[6])
            if slope is not None:
                weighted_slope += slope * area
                weight_slope += area

    # SWMM area unit defaults vary by FLOW_UNITS; we ship as-is and let
    # the caller pre-normalise if cross-unit comparisons matter. The
    # similarity scorer operates in log space so a uniform multiplier
    # cancels out, which is the common case.
    area_ha = total_area
    imperv_pct = (weighted_imperv / weight_imperv) if weight_imperv > 0 else 0.0
    mean_slope_pct = (weighted_slope / weight_slope) if weight_slope > 0 else 0.0

    return WatershedAttributes(
        area_ha=area_ha,
        imperv_pct=imperv_pct,
        mean_slope_pct=mean_slope_pct,
        n_subcatchments=n_subcatchments,
        n_conduits=len(sections.get("CONDUITS", [])),
        n_outfalls=len(sections.get("OUTFALLS", [])),
    )


# Default feature weights. Equal weighting is the conservative starting
# point; downstream callers can re-derive bespoke weights once enough
# cases land in calibration_memory to learn them. Sum need not be 1;
# the score normalisation handles that.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "log_area": 1.0,
    "imperv_pct": 1.0,
    "mean_slope_pct": 1.0,
    "log_n_subcatchments": 1.0,
    "log_n_conduits": 1.0,
}


def _log1p_nonneg(x: float) -> float:
    """``log(1 + max(x, 0))`` — keeps log-space features finite at zero."""
    return math.log1p(max(x, 0.0))


def _feature_vector(attrs: WatershedAttributes) -> dict[str, float]:
    return {
        "log_area": _log1p_nonneg(attrs.area_ha),
        "imperv_pct": float(attrs.imperv_pct),
        "mean_slope_pct": float(attrs.mean_slope_pct),
        "log_n_subcatchments": _log1p_nonneg(float(attrs.n_subcatchments)),
        "log_n_conduits": _log1p_nonneg(float(attrs.n_conduits)),
    }


def similarity_score(a: WatershedAttributes, b: WatershedAttributes) -> float:
    """Return a similarity score in ``[0, 1]`` (1 = identical).

    Weighted L2 distance between the two feature vectors, divided by
    the L2 norm of the larger vector so the result is unit-free, then
    squashed via ``1 / (1 + d)``. Identical inputs score exactly
    ``1.0``; large watersheds compared to tiny ones converge toward
    ``0`` without crossing it.
    """
    fa = _feature_vector(a)
    fb = _feature_vector(b)
    weights = _DEFAULT_WEIGHTS

    # Normalise each feature by the larger magnitude before differencing
    # so the score does not collapse to "area dominates". A floor of 1
    # keeps zero-everywhere comparisons finite (and they score 1.0).
    sq_distance = 0.0
    for key, w in weights.items():
        va = fa[key]
        vb = fb[key]
        scale = max(abs(va), abs(vb), 1.0)
        delta = (va - vb) / scale
        sq_distance += w * (delta * delta)
    distance = math.sqrt(sq_distance)
    return 1.0 / (1.0 + distance)


def rank_similar_cases(
    target: WatershedAttributes,
    candidates: dict[str, WatershedAttributes],
    *,
    top_k: int = 3,
) -> list[tuple[str, float]]:
    """Return up to ``top_k`` ``(case_name, score)`` pairs, highest first.

    ``candidates`` is keyed by the case-name string the caller will
    use to index back into ``parametric_memory`` / ``calibration_memory``.
    Ties are broken by case-name lexicographic order for determinism.
    """
    if top_k <= 0:
        return []
    scored = [
        (name, similarity_score(target, attrs)) for name, attrs in candidates.items()
    ]
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored[:top_k]
