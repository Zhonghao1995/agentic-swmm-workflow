"""Algorithmic design-storm generator (PRD-06 Phase B.4).

A modeler asks: "I need a 1-hour, 25 mm storm in SWMM DAT format."
``generate_design_storm`` returns an in-memory :class:`DesignStorm`
with intensities at the requested interval; ``to_swmm_dat`` emits the
SWMM ``[TIMESERIES]`` text the modeler pastes into an INP.

Scope (intentional)
-------------------
- Shapes: ``"uniform"``, ``"triangular"`` (peak at midpoint),
  ``"front_loaded"`` (peak at 25%), ``"back_loaded"`` (peak at 75%),
  plus ``chicago`` / ``huff`` / ``scs`` hyetographs (Round 2, #157).
- Conservation: ``sum(intensity * dt_hr) == depth_mm`` for every
  explicit-depth shape. Tests pin this contract.
- IDF input: ``chicago_hyetograph(idf_params={"a", "b", "c"})`` builds
  the exact Keifer–Chu storm from ``i = a/(t+b)^c`` (storm total = the
  IDF depth of the full duration). No curve fitting or return-period
  inference here — fitted regional curves stay upstream (storm library
  / ``aiswmm storm --idf``).

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


# ---------------------------------------------------------------------------
# Dimensionless mass curves for engineering hyetographs.
# ---------------------------------------------------------------------------
# The four Huff quartiles each provide a cumulative-mass distribution that
# rises from 0 at t=0 to 1.0 at t=duration. Each tuple is the cumulative
# *fraction* of total depth at 10%, 20%, ..., 100% of the storm duration.
# Q1 front-loads the peak in the first quarter, Q4 back-loads it.
# Embedded as a coding convenience; values widely tabulated in the storm
# engineering literature.
_HUFF_CUMULATIVE = {
    1: (0.18, 0.37, 0.58, 0.74, 0.84, 0.90, 0.94, 0.97, 0.99, 1.00),
    2: (0.07, 0.18, 0.35, 0.56, 0.76, 0.87, 0.93, 0.96, 0.98, 1.00),
    3: (0.04, 0.10, 0.17, 0.26, 0.40, 0.58, 0.78, 0.91, 0.97, 1.00),
    4: (0.03, 0.06, 0.10, 0.16, 0.24, 0.34, 0.45, 0.61, 0.81, 1.00),
}


# SCS Type II 24-hr dimensionless mass curve at 1-hour increments
# (0..24). Each pair is (hours_from_storm_start, cumulative_fraction).
# Total depth is delivered between t=0 and t=24h; the steepest rise
# (the peak) sits at t=12h (midpoint). The cumulative curve is
# linearly interpolated in :func:`_scs_cumulative_at` so any
# ``interval_min`` divisor produces a smooth hyetograph.
_SCS_TYPE_II_24H_HOURLY = (
    (0.0, 0.000),
    (1.0, 0.011),
    (2.0, 0.022),
    (3.0, 0.035),
    (4.0, 0.048),
    (5.0, 0.064),
    (6.0, 0.080),
    (7.0, 0.098),
    (8.0, 0.120),
    (9.0, 0.147),
    (10.0, 0.181),
    (11.0, 0.236),
    (11.5, 0.283),
    (11.75, 0.357),
    (12.0, 0.663),
    (12.5, 0.735),
    (13.0, 0.772),
    (13.5, 0.799),
    (14.0, 0.820),
    (15.0, 0.854),
    (16.0, 0.880),
    (17.0, 0.898),
    (18.0, 0.915),
    (19.0, 0.930),
    (20.0, 0.944),
    (21.0, 0.958),
    (22.0, 0.971),
    (23.0, 0.985),
    (24.0, 1.000),
)


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


def chicago_hyetograph(
    *,
    depth_mm: float | None = None,
    idf_params: dict[str, float] | None = None,
    duration_min: int,
    peak_position: float = 0.5,
    interval_min: int = 5,
    start_time: str = "2000-01-01 00:00",
) -> DesignStorm:
    """Build a Chicago hyetograph (peak-centred, mirror-distributed).

    The Chicago method places the peak intensity at ``peak_position *
    duration``, then mirror-distributes equal-volume slices around it.
    Two construction modes are supported:

    1. **Depth + duration**: pass ``depth_mm`` plus ``duration_min``.
       A generic Chicago shape is built whose total depth equals the
       requested ``depth_mm`` and whose peak lands at ``peak_position``
       of the duration (default 0.5; common regional values are 0.4 for
       a Vancouver-class climate or about 0.375 for the US Midwest).

    2. **IDF parameters + duration**: pass ``idf_params`` as a dict
       ``{"a": ..., "b": ..., "c": ...}`` for the standard
       intensity-duration formula ``i = a / (t + b)^c``. The function
       integrates that curve to produce the matching depth and lays
       out a Chicago hyetograph at the requested peak position.

    ``peak_position`` must satisfy ``0 < peak_position < 1`` so both
    legs have at least one ordinate. The function preserves total
    depth: ``sum(intensity * dt_hr) ≈ depth``.
    """
    if duration_min <= 0:
        raise ValueError("duration_min must be positive")
    if interval_min <= 0:
        raise ValueError("interval_min must be positive")
    if duration_min % interval_min != 0:
        raise ValueError(
            f"duration_min ({duration_min}) must be a multiple of "
            f"interval_min ({interval_min}) so the last step is not truncated"
        )
    if not (0.0 < peak_position < 1.0):
        raise ValueError("peak_position must be strictly between 0 and 1")

    if depth_mm is None and idf_params is None:
        raise ValueError("provide either depth_mm or idf_params")
    if depth_mm is not None and idf_params is not None:
        raise ValueError("pass depth_mm OR idf_params, not both")

    n_steps = duration_min // interval_min
    dt_hr = interval_min / 60.0

    if idf_params is not None:
        try:
            a = float(idf_params["a"])
            b = float(idf_params["b"])
            c = float(idf_params["c"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "idf_params must contain numeric 'a', 'b', and 'c'"
            ) from exc
        if a <= 0 or c <= 0:
            raise ValueError("idf_params 'a' and 'c' must be positive")

        intensities = _chicago_from_idf(
            a=a,
            b=b,
            c=c,
            duration_min=duration_min,
            peak_position=peak_position,
            interval_min=interval_min,
        )
        resolved_depth = sum(i * dt_hr for i in intensities)
    else:
        # depth-driven Chicago: use the IDF mode with synthetic a/b/c
        # that integrate exactly to ``depth_mm``. Pick c=0.75 (common
        # mid-range exponent) and b=0; scale ``a`` so the integral
        # over duration matches depth_mm. The math:
        #   depth = ∫ a / t^c dt from 0 to T (b=0 makes it integrable
        #   via the same routine and avoids divide-by-zero by binning).
        # We instead synthesise a unit-area Chicago shape and scale.
        assert depth_mm is not None  # for type narrowing
        if depth_mm < 0:
            raise ValueError("depth_mm must be non-negative")
        intensities = _chicago_unit(
            duration_min=duration_min,
            peak_position=peak_position,
            interval_min=interval_min,
        )
        weight_sum = sum(intensities)
        if weight_sum <= 0:
            intensities = [0.0] * n_steps
        else:
            # scale so sum(intensity * dt_hr) == depth_mm
            scale = depth_mm / (weight_sum * dt_hr)
            intensities = [v * scale for v in intensities]
        resolved_depth = depth_mm

    start_dt = _parse_start_time(start_time)
    times = [
        (start_dt + timedelta(minutes=step * interval_min)).strftime(
            _SWMM_TS_FORMAT
        )
        for step in range(n_steps)
    ]

    metadata: dict[str, Any] = {
        "start_time_iso": start_dt.isoformat(timespec="minutes"),
        "n_steps": n_steps,
        "peak_position": peak_position,
        "construction": "idf_params" if idf_params is not None else "depth",
    }
    if idf_params is not None:
        metadata["idf_params"] = {"a": a, "b": b, "c": c}

    return DesignStorm(
        times=times,
        intensities_mm_per_hr=intensities,
        depth_mm=resolved_depth,
        duration_min=duration_min,
        shape="chicago",
        interval_min=interval_min,
        metadata=metadata,
    )


def huff_hyetograph(
    *,
    depth_mm: float,
    duration_min: int,
    quartile: int,
    interval_min: int = 5,
    start_time: str = "2000-01-01 00:00",
) -> DesignStorm:
    """Build a Huff quartile hyetograph for the requested storm depth.

    ``quartile`` is the integer 1..4 identifying which quarter of the
    storm holds the peak intensity (Q1 = front-loaded peak in the first
    quarter, Q4 = back-loaded peak in the fourth quarter). The function
    uses the dimensionless cumulative-mass table embedded in this
    module, linearly interpolated to the requested step count.

    Total depth is preserved (``sum(intensity * dt_hr) ≈ depth_mm``).
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
    if quartile not in (1, 2, 3, 4):
        raise ValueError(f"quartile must be 1, 2, 3, or 4 — got {quartile!r}")

    n_steps = duration_min // interval_min
    dt_hr = interval_min / 60.0
    cumulative = _HUFF_CUMULATIVE[quartile]

    # Cumulative fraction at the *end* of each step. Step k covers
    # ``[(k/n)*duration, ((k+1)/n)*duration]``; interpolate from the
    # 10-point table at fraction ``(k+1)/n``.
    intensities: list[float] = []
    prev_frac = 0.0
    for step in range(n_steps):
        end_fraction = (step + 1) / n_steps
        cur_frac = _interp_huff(cumulative, end_fraction)
        # Step depth (mm) = (cur_frac - prev_frac) * depth_mm;
        # intensity (mm/hr) = step_depth / dt_hr.
        step_depth = (cur_frac - prev_frac) * depth_mm
        intensities.append(step_depth / dt_hr if dt_hr > 0 else 0.0)
        prev_frac = cur_frac

    start_dt = _parse_start_time(start_time)
    times = [
        (start_dt + timedelta(minutes=step * interval_min)).strftime(
            _SWMM_TS_FORMAT
        )
        for step in range(n_steps)
    ]

    metadata = {
        "start_time_iso": start_dt.isoformat(timespec="minutes"),
        "n_steps": n_steps,
        "quartile": quartile,
    }
    return DesignStorm(
        times=times,
        intensities_mm_per_hr=intensities,
        depth_mm=depth_mm,
        duration_min=duration_min,
        shape="huff",
        interval_min=interval_min,
        metadata=metadata,
    )


def scs_type_ii_hyetograph(
    *,
    depth_mm: float,
    duration_min: int = 1440,
    interval_min: int = 5,
    start_time: str = "2000-01-01 00:00",
) -> DesignStorm:
    """Build the SCS Type II 24-hour hyetograph at ``interval_min`` steps.

    The dimensionless mass curve is the standard 24-hr Type II
    distribution (peak around t=12 hours). The curve is linearly
    interpolated to the requested step count and scaled to the
    requested total depth.

    Defaults to ``duration_min=1440`` (24 hours). Other durations are
    accepted (the curve is rescaled to the requested span) so the
    caller can produce, e.g., a 12-hr or 48-hr Type-II-shaped storm,
    but the canonical use is 24-hr.
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

    n_steps = duration_min // interval_min
    dt_hr = interval_min / 60.0
    total_hours = duration_min / 60.0

    intensities: list[float] = []
    prev_frac = 0.0
    for step in range(n_steps):
        # Scale step end time onto the table's [0, 24] domain.
        end_hours = ((step + 1) / n_steps) * 24.0
        cur_frac = _scs_cumulative_at(end_hours)
        step_depth = (cur_frac - prev_frac) * depth_mm
        intensities.append(step_depth / dt_hr if dt_hr > 0 else 0.0)
        prev_frac = cur_frac

    start_dt = _parse_start_time(start_time)
    times = [
        (start_dt + timedelta(minutes=step * interval_min)).strftime(
            _SWMM_TS_FORMAT
        )
        for step in range(n_steps)
    ]

    metadata = {
        "start_time_iso": start_dt.isoformat(timespec="minutes"),
        "n_steps": n_steps,
        "scs_total_hours": total_hours,
    }
    return DesignStorm(
        times=times,
        intensities_mm_per_hr=intensities,
        depth_mm=depth_mm,
        duration_min=duration_min,
        shape="scs_type_ii",
        interval_min=interval_min,
        metadata=metadata,
    )


def _chicago_unit(
    *,
    duration_min: int,
    peak_position: float,
    interval_min: int,
) -> list[float]:
    """Build a unit Chicago shape (intensity *weights*, not mm/hr).

    The Chicago method is asymmetric-mirror around the peak. We
    construct it by computing how long *before* and *after* the peak
    each bin sits, then assigning intensity proportional to
    ``1 / (lead_or_lag)^0.75`` so the curve is monotone-rising up to
    the peak and monotone-falling after. The exponent 0.75 produces a
    realistic peakedness; the caller scales to the desired depth so
    the absolute intensity is determined by total depth, not by this
    exponent.
    """
    n_steps = duration_min // interval_min
    if n_steps <= 0:
        return []
    peak_t = peak_position * duration_min  # peak time in minutes from start

    weights: list[float] = []
    for step in range(n_steps):
        # Use bin centres so the curve is smooth.
        bin_centre = (step + 0.5) * interval_min
        # Distance from peak in minutes. Small ε prevents the peak bin
        # from blowing up to infinity.
        delta = abs(bin_centre - peak_t)
        # Asymmetric stretch: a point ε*duration before the peak should
        # rise as fast as a point ε*duration after it falls, but with
        # the legs having different *lengths*. We normalise distance by
        # the leg length so both legs reach the same minimum intensity.
        if bin_centre <= peak_t:
            leg = max(peak_t, 1e-6)
        else:
            leg = max(duration_min - peak_t, 1e-6)
        normalised_delta = delta / leg
        # Avoid divide-by-zero at the peak; cap at the bin-half width.
        denom = max(normalised_delta, 0.5 / n_steps)
        # Chicago peakedness exponent. Higher → sharper peak.
        weights.append(1.0 / (denom ** 0.75))
    return weights


def _chicago_from_idf(
    *,
    a: float,
    b: float,
    c: float,
    duration_min: int,
    peak_position: float,
    interval_min: int,
) -> list[float]:
    """Build the Keifer–Chu Chicago hyetograph from IDF ``i = a/(t+b)^c``.

    ``t`` is in minutes, ``i`` in mm/hr. Defining property: any window of
    duration ``tau`` centred on the peak (``r*tau`` before, ``(1-r)*tau``
    after, with ``r = peak_position``) accumulates exactly the IDF depth
    ``D(tau) = i(tau) * tau / 60`` — so the storm total is exactly
    ``D(duration_min)``. Construction: limb cumulatives
    ``C_pre(s) = r*D(s/r)`` / ``C_post(s) = (1-r)*D(s/(1-r))`` are
    differenced at bin edges, which conserves mass exactly at any
    discretisation and leaves no dry bin when the peak lands on a bin
    boundary.

    Twin implementation: ``skills/swmm-climate/scripts/design_storm.py``
    ``chicago_hyetograph`` (that script's stdlib-only portability
    constraint forbids sharing code; ``tests/test_chicago_idf_parity.py``
    locks the two numerically equal).
    """
    n_steps = duration_min // interval_min
    if n_steps <= 0:
        return []
    r = peak_position
    t_pre = r * duration_min

    def idf_depth(t_branch: float) -> float:
        """Cumulative IDF depth (mm) over duration ``t_branch`` minutes."""
        if t_branch <= 0.0:
            return 0.0
        base = t_branch + b
        if base <= 0.0:
            return 0.0
        return (a / (base ** c)) * t_branch / 60.0

    def limb_pre(s: float) -> float:
        return r * idf_depth(s / r)

    def limb_post(s: float) -> float:
        return (1.0 - r) * idf_depth(s / (1.0 - r))

    def cumulative(t: float) -> float:
        if t <= t_pre:
            return limb_pre(t_pre) - limb_pre(t_pre - t)
        return limb_pre(t_pre) + limb_post(t - t_pre)

    dt_hr = interval_min / 60.0
    intensities: list[float] = []
    for k in range(n_steps):
        lo = float(k * interval_min)
        hi = float(duration_min) if k == n_steps - 1 else float((k + 1) * interval_min)
        depth = max(0.0, cumulative(hi) - cumulative(lo))
        intensities.append(depth / dt_hr)
    return intensities


def _interp_huff(cumulative: tuple[float, ...], fraction: float) -> float:
    """Interpolate the 10-point Huff cumulative table at ``fraction``.

    ``cumulative`` holds values at 10%, 20%, ..., 100%. We treat
    fraction 0.0 as cumulative 0.0; below 10% we linearly interpolate
    between (0.0, 0.0) and the first table entry. Above 100% we cap at
    1.0.
    """
    if fraction <= 0.0:
        return 0.0
    if fraction >= 1.0:
        return float(cumulative[-1])
    # Each table entry sits at fraction (k+1) * 0.1 for k=0..9.
    pos = fraction * 10.0
    lower_idx = int(pos) - 1  # 0-based table index immediately below
    upper_idx = lower_idx + 1
    if lower_idx < 0:
        # Between 0.0 and 0.1 — interp from (0, 0) to cumulative[0].
        return cumulative[0] * (fraction / 0.1)
    if upper_idx >= len(cumulative):
        return float(cumulative[-1])
    lower_frac = (lower_idx + 1) * 0.1
    upper_frac = (upper_idx + 1) * 0.1
    span = upper_frac - lower_frac
    if span <= 0:
        return float(cumulative[lower_idx])
    weight = (fraction - lower_frac) / span
    return (
        float(cumulative[lower_idx])
        + weight * (float(cumulative[upper_idx]) - float(cumulative[lower_idx]))
    )


def _scs_cumulative_at(hours: float) -> float:
    """Return the SCS Type II cumulative fraction at ``hours`` (0..24).

    Linearly interpolated between table breakpoints. Values outside
    the [0, 24] domain are clipped.
    """
    if hours <= 0.0:
        return 0.0
    if hours >= 24.0:
        return 1.0
    # Walk the table to find the bracketing pair.
    for idx in range(len(_SCS_TYPE_II_24H_HOURLY) - 1):
        t0, f0 = _SCS_TYPE_II_24H_HOURLY[idx]
        t1, f1 = _SCS_TYPE_II_24H_HOURLY[idx + 1]
        if t0 <= hours <= t1:
            if t1 == t0:
                return f0
            weight = (hours - t0) / (t1 - t0)
            return f0 + weight * (f1 - f0)
    return 1.0


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
    # PRD-08 Phase B (audit #29): the "Value" column was ambiguous
    # between intensity (mm/h) and per-step depth. SWMM's TIMESERIES
    # default for RAINFALL series is INTENSITY, so spell that out in
    # the header so a modeler reading the DAT block immediately knows
    # how to interpret the column.
    lines.append(
        ";; values are intensity in mm/h "
        "(SWMM TIMESERIES default with RAINFALL kind = INTENSITY)"
    )
    lines.append(";;Name           Date       Time      Value")
    lines.append(";;-------------- ---------- --------- -------")
    for stamp, intensity in zip(storm.times, storm.intensities_mm_per_hr):
        date_part, time_part = stamp.split(" ", 1)
        lines.append(
            f"{station_id:<15} {date_part} {time_part:<9} {intensity:.4f}"
        )
    return "\n".join(lines) + "\n"
