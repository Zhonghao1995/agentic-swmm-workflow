"""Algorithmic design-storm generator (PRD-06 Phase B.4).

A modeler asks: "I need a 1-hour, 25 mm storm in SWMM DAT format."
``generate_design_storm`` returns an in-memory :class:`DesignStorm`
with intensities at the requested interval; ``to_swmm_dat`` emits the
SWMM ``[TIMESERIES]`` text the modeler pastes into an INP.

Scope (intentional)
-------------------
- Shapes: ``"uniform"``, ``"triangular"`` (peak at midpoint),
  ``"front_loaded"`` (peak at 25%), ``"back_loaded"`` (peak at 75%).
- Conservation: ``sum(intensity * dt_hr) == depth_mm`` for every
  shape. Tests pin this contract.
- No IDF curve lookup, no curve-number transform, no return-period
  inference. Real-world design storms typically come from local IDF
  curves and require curve fitting — that is later-phase work. The
  scope here is "give me a shape with a known depth and duration"
  which is what the modeler actually needs interactively.

Why this lives in ``agent/swmm_runtime``
----------------------------------------
The verb is deterministic over pure functions and has no SWMM-binary
dependency. The same pattern as :mod:`compare` keeps the SWMM-domain
verbs siloed from the agent planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


# Shapes we handle inline. Anything outside this set raises a
# ``ValueError`` so a typo at the CLI does not silently produce a
# uniform storm.
_SHAPES = ("uniform", "triangular", "front_loaded", "back_loaded")


# SWMM's [TIMESERIES] block prefers ``MM/DD/YYYY HH:MM`` separated by
# whitespace. Most existing INP fixtures in the repo use this exact
# format. Keep it isolated so a future schema migration is one place.
_SWMM_TS_FORMAT = "%m/%d/%Y %H:%M"


@dataclass
class DesignStorm:
    """Result of :func:`generate_design_storm`.

    ``times`` are SWMM ``MM/DD/YYYY HH:MM`` strings indexed at the
    *start* of each interval. ``intensities_mm_per_hr`` is the average
    intensity over that interval. The list lengths are equal:
    ``duration_min // interval_min``.
    """

    times: list[str]
    intensities_mm_per_hr: list[float]
    depth_mm: float
    duration_min: int
    shape: str
    interval_min: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "times": list(self.times),
            "intensities_mm_per_hr": list(self.intensities_mm_per_hr),
            "depth_mm": self.depth_mm,
            "duration_min": self.duration_min,
            "shape": self.shape,
            "interval_min": self.interval_min,
            "metadata": dict(self.metadata),
        }


def generate_design_storm(
    *,
    depth_mm: float,
    duration_min: int,
    shape: str = "uniform",
    interval_min: int = 5,
    start_time: str = "2000-01-01 00:00",
) -> DesignStorm:
    """Build a :class:`DesignStorm` with the requested shape + depth.

    ``depth_mm`` is total rainfall depth in millimetres. ``duration_min``
    must be a positive multiple of ``interval_min``; otherwise the
    last partial step would silently drop volume.

    Shapes
    ------
    - ``"uniform"``: every step gets ``depth_mm / duration_hr`` intensity.
    - ``"triangular"``: symmetric tent — intensity rises linearly to
      a peak at the midpoint, then falls back to zero. Normalised so
      ``sum(intensity * dt) == depth_mm``.
    - ``"front_loaded"``: triangular with peak at 25% of duration.
    - ``"back_loaded"``: triangular with peak at 75% of duration.

    ``start_time`` is ``"YYYY-MM-DD HH:MM"`` (a generous parse — we
    let :meth:`datetime.fromisoformat` do the work, then re-emit in
    SWMM's preferred date string).
    """
    if depth_mm < 0:
        raise ValueError("depth_mm must be non-negative")
    if duration_min <= 0:
        raise ValueError("duration_min must be positive")
    if interval_min <= 0:
        raise ValueError("interval_min must be positive")
    if duration_min % interval_min != 0:
        raise ValueError(
            f"duration_min ({duration_min}) must be a multiple of "
            f"interval_min ({interval_min}) so the last step is not truncated"
        )
    if shape not in _SHAPES:
        raise ValueError(
            f"shape must be one of {_SHAPES}, got {shape!r}"
        )

    n_steps = duration_min // interval_min
    dt_hr = interval_min / 60.0

    if shape == "uniform":
        intensity = depth_mm / (duration_min / 60.0)
        intensities = [intensity] * n_steps
    elif shape == "triangular":
        intensities = _triangular_intensities(
            depth_mm=depth_mm, n_steps=n_steps, peak_position=0.5, dt_hr=dt_hr
        )
    elif shape == "front_loaded":
        intensities = _triangular_intensities(
            depth_mm=depth_mm, n_steps=n_steps, peak_position=0.25, dt_hr=dt_hr
        )
    else:  # back_loaded — exhaustive by the _SHAPES tuple above
        intensities = _triangular_intensities(
            depth_mm=depth_mm, n_steps=n_steps, peak_position=0.75, dt_hr=dt_hr
        )

    start_dt = _parse_start_time(start_time)
    times: list[str] = []
    for step in range(n_steps):
        stamp = start_dt + timedelta(minutes=step * interval_min)
        times.append(stamp.strftime(_SWMM_TS_FORMAT))

    metadata = {
        "start_time_iso": start_dt.isoformat(timespec="minutes"),
        "n_steps": n_steps,
        "peak_position": _peak_position_for(shape),
    }
    return DesignStorm(
        times=times,
        intensities_mm_per_hr=intensities,
        depth_mm=depth_mm,
        duration_min=duration_min,
        shape=shape,
        interval_min=interval_min,
        metadata=metadata,
    )


def _peak_position_for(shape: str) -> float | None:
    """Return the fractional peak position the shape encodes."""
    if shape == "uniform":
        return None
    if shape == "triangular":
        return 0.5
    if shape == "front_loaded":
        return 0.25
    if shape == "back_loaded":
        return 0.75
    return None  # unreachable given _SHAPES gating


def _parse_start_time(text: str) -> datetime:
    """Parse the user-supplied start_time.

    We accept both ISO 8601 (``"2000-01-01T00:00"``) and the
    space-separated form (``"2000-01-01 00:00"``). ``fromisoformat``
    on 3.11+ handles the space; older runtimes get a manual fallback.
    """
    text = text.strip()
    try:
        return datetime.fromisoformat(text.replace("T", " "))
    except ValueError:
        # Fallback: try the SWMM format directly.
        return datetime.strptime(text, _SWMM_TS_FORMAT)


def _triangular_intensities(
    *, depth_mm: float, n_steps: int, peak_position: float, dt_hr: float
) -> list[float]:
    """Build a triangular hyetograph with peak at ``peak_position * n_steps``.

    We construct unit-area weights then scale so the sum-times-dt
    equals ``depth_mm``. Both legs of the triangle are linear from 0
    to the peak; the peak height is set by the conservation constraint
    so we never need to special-case ``peak_position == 0`` or ``1``.
    """
    if n_steps <= 0:
        return []

    # Use bin-centre positions so the curve is symmetric around the
    # peak for ``peak_position == 0.5`` regardless of n_steps parity.
    peak_idx_frac = peak_position * n_steps
    weights: list[float] = []
    for step in range(n_steps):
        centre = step + 0.5
        # Linear ramp from 0 at the boundary to 1 at the peak idx,
        # decoupled for left / right legs so asymmetric peaks work.
        if centre <= peak_idx_frac:
            denom = max(peak_idx_frac, 1e-9)
            weight = centre / denom
        else:
            denom = max(n_steps - peak_idx_frac, 1e-9)
            weight = (n_steps - centre) / denom
        weight = max(weight, 0.0)
        weights.append(weight)

    # Normalise so sum(weight * peak * dt) == depth_mm. Solve for
    # peak: peak = depth_mm / (sum(weights) * dt_hr).
    weight_sum = sum(weights)
    if weight_sum <= 0:
        return [0.0] * n_steps
    peak = depth_mm / (weight_sum * dt_hr)
    return [w * peak for w in weights]


def to_swmm_dat(
    storm: DesignStorm,
    *,
    station_id: str = "STN1",
) -> str:
    """Return SWMM ``[TIMESERIES]`` block text for ``storm``.

    The output is a header line tagging the series + one row per
    interval. Modelers paste this directly under their INP's
    ``[TIMESERIES]`` section. We emit two columns (date + time) so the
    series is unambiguous when the storm crosses midnight.
    """
    lines: list[str] = []
    lines.append(
        f";; Design storm — {storm.depth_mm:.2f} mm over "
        f"{storm.duration_min} min, shape={storm.shape}, "
        f"interval={storm.interval_min} min"
    )
    lines.append(";;Name           Date       Time      Value")
    lines.append(";;-------------- ---------- --------- -------")
    for stamp, intensity in zip(storm.times, storm.intensities_mm_per_hr):
        date_part, time_part = stamp.split(" ", 1)
        lines.append(
            f"{station_id:<15} {date_part} {time_part:<9} {intensity:.4f}"
        )
    return "\n".join(lines) + "\n"
