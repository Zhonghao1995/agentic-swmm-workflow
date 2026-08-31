"""Band model for the graded HITL gate (spec: fuzzy-hitl-gates).

Three severity bands per numeric pattern (low / medium / high), built
from three anchors: the fine boundary, the centre of the uncertain
band, and the bad boundary. ``Bands.from_spec`` is deliberately quiet
on malformed input (returns ``None``) so a misconfigured entry falls
back to the crisp comparison instead of weakening or crashing the
gate; the evaluator owns that fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_LEVELS = ("low", "medium", "high")
_DIRECTIONS = ("higher_is_worse", "higher_is_better")


@dataclass(frozen=True)
class Bands:
    fine: float
    centre: float
    bad: float
    direction: str = "higher_is_worse"

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> "Bands | None":
        raw = spec.get("bands")
        if not isinstance(raw, dict):
            return None
        try:
            fine = float(raw["fine"])
            centre = float(raw["centre"])
            bad = float(raw["bad"])
        except (KeyError, TypeError, ValueError):
            return None
        direction = str(spec.get("direction") or "higher_is_worse")
        if direction not in _DIRECTIONS:
            return None
        sign = -1.0 if direction == "higher_is_better" else 1.0
        # Anchors must be strictly ordered along the badness axis, or
        # the membership slopes below divide by zero / invert.
        if not (sign * fine < sign * centre < sign * bad):
            return None
        return cls(fine=fine, centre=centre, bad=bad, direction=direction)


def memberships(value: float, bands: Bands) -> dict[str, float]:
    """Shoulder-triangle-shoulder membership of ``value`` in each band."""
    sign = -1.0 if bands.direction == "higher_is_better" else 1.0
    v = sign * float(value)
    f, c, b = sign * bands.fine, sign * bands.centre, sign * bands.bad
    low = 1.0 if v <= f else max(0.0, (c - v) / (c - f))
    if v <= f or v >= b:
        medium = 0.0
    elif v <= c:
        medium = (v - f) / (c - f)
    else:
        medium = (b - v) / (b - c)
    high = 1.0 if v >= b else max(0.0, (v - c) / (b - c))
    return {"low": low, "medium": medium, "high": high}


def grade(value: float, bands: Bands) -> tuple[dict[str, float], str]:
    """Return ``(memberships, level)``; membership ties resolve severe.

    Ties are decided on memberships rounded to 1e-12: a value exactly
    halfway between two anchors is a true tie in real arithmetic, and
    without the rounding, float noise picks a side arbitrarily (KGE 0.4
    against the 0.5 / 0.3 anchors landed epsilon-medium instead of the
    severe band).
    """
    m = memberships(value, bands)
    level = max(_LEVELS, key=lambda k: (round(m[k], 12), _LEVELS.index(k)))
    return m, level
