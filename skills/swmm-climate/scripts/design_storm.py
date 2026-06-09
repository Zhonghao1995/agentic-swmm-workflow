#!/usr/bin/env python3
"""Generate synthetic design-storm hyetographs (Chicago / alternating-block).

Output contract matches ``format_rainfall.py`` ``--out-json`` + ``--out-timeseries`` shape
so that ``build_swmm_inp.py --rainfall-json`` consumes the result unchanged.

Stdlib-only; zero ``agentic_swmm`` imports (portability constraint identical to the
other two scripts in this directory).

Determinism guarantee: no ``datetime.now()``, no ``random``, no network I/O.
Same args always produce byte-identical output files.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# IDF intensity helpers
# ---------------------------------------------------------------------------

def _idf_cn_form(t_min: float, *, A1: float, C: float, lgP: float, b: float, n: float) -> float:
    """Chicago/Chinese CN formula.

    q = 167 · A1 · (1 + C · lgP) / (t + b)^n   [L/s/ha]

    Convert to mm/hr:  mm/hr = q × 0.36
    (because L/s/ha × 3600 s/hr × 1 mm/1 L·m⁻² × 1/10 000 ha/m² … works out to ×0.36)
    """
    q_lsha = 167.0 * A1 * (1.0 + C * lgP) / ((t_min + b) ** n)
    return q_lsha * 0.36  # mm/hr


def _idf_generic_form(t_min: float, *, a: float, b: float, c: float) -> float:
    """Generic IDF formula.

    i = a / (t + b)^c   [mm/hr]
    """
    return a / ((t_min + b) ** c)


# ---------------------------------------------------------------------------
# Chicago / Keifer-Chu hyetograph
# ---------------------------------------------------------------------------

def chicago_hyetograph(
    *,
    coefficients: dict[str, float],
    form: str,
    return_period_yr: float,
    duration_min: float,
    dt_min: float,
    r: float,
) -> list[float]:
    """Return a list of depth increments (mm per timestep) for the Chicago hyetograph.

    The hyetograph is discretised at ``dt_min`` resolution; ``len(result) == n_steps``
    where ``n_steps = round(duration_min / dt_min)``.

    The peak block lands at index ``floor(r * n_steps)`` or ``floor(r * n_steps) + 1``,
    which is within one dt of ``r * duration_min`` (standard Chicago convention).

    **Mass conservation (Keifer-Chu defining property)**: the sum of all
    depth increments equals ``IDF_depth(duration_min)`` — the IDF cumulative
    depth for the design duration itself. The limbs carry r-weighted shares:
    the rising limb totals ``r * IDF_depth(T)`` and the falling limb
    ``(1 - r) * IDF_depth(T)``, because any window of duration tau centred
    on the peak (r*tau before, (1-r)*tau after) must accumulate exactly
    ``IDF_depth(tau)``. Limb cumulatives are therefore
    ``C_pre(s) = r * IDF_depth(s / r)`` and
    ``C_post(s) = (1 - r) * IDF_depth(s / (1 - r))``.

    Parameters
    ----------
    coefficients:
        For ``form="CN"``:  keys ``A1``, ``C``, ``b``, ``n``
        (``lgP`` is derived from ``return_period_yr``).
        For ``form="generic"``: keys ``a``, ``b``, ``c``.
    form:
        ``"CN"`` or ``"generic"``.
    return_period_yr:
        Return period in years (used only for CN form).
    duration_min:
        Total storm duration in minutes.
    dt_min:
        Timestep in minutes.
    r:
        Peak-position ratio (0 < r < 1, default 0.4).

    Returns
    -------
    list[float]
        Depth increments in mm per timestep, length == ``round(duration_min / dt_min)``.
    """
    n_steps = round(duration_min / dt_min)
    if n_steps < 1:
        raise ValueError(f"duration_min={duration_min} / dt_min={dt_min} must yield >= 1 step")

    form_upper = form.upper()
    lgP = math.log10(return_period_yr) if return_period_yr > 0 else 0.0

    def idf_depth(t_branch: float) -> float:
        """Cumulative IDF depth (mm) for branch duration t_branch (minutes).

        D(t) = i(t) * t / 60   where i(t) is average intensity over duration t [mm/hr].
        """
        if t_branch <= 0.0:
            return 0.0
        if form_upper == "CN":
            i_t = _idf_cn_form(
                t_branch,
                A1=coefficients["A1"],
                C=coefficients["C"],
                lgP=lgP,
                b=coefficients["b"],
                n=coefficients["n"],
            )
        elif form_upper == "GENERIC":
            i_t = _idf_generic_form(
                t_branch,
                a=coefficients["a"],
                b=coefficients["b"],
                c=coefficients["c"],
            )
        else:
            raise ValueError(f"Unknown IDF form '{form}'. Use 'CN' or 'generic'.")
        return i_t * t_branch / 60.0  # mm

    # Keifer-Chu limb cumulatives. A window of duration tau centred on the
    # peak (r*tau before, (1-r)*tau after) must accumulate exactly D(tau),
    # so the limb cumulative at distance s from the peak is the r-weighted
    # share of the window it closes: C_pre(s) = r * D(s/r) and
    # C_post(s) = (1-r) * D(s/(1-r)). Telescoping the block differences
    # makes the storm total exactly C_pre(rT) + C_post((1-r)T) = D(T).
    def limb_pre(s: float) -> float:
        return r * idf_depth(s / r)

    def limb_post(s: float) -> float:
        return (1.0 - r) * idf_depth(s / (1.0 - r))

    # Rising limb block k (0-indexed left to right, k in 0..i_peak):
    #   distance from peak at block END (nearer peak)  = t_pre - k * dt
    #   distance from peak at block START (farther)    = t_pre - (k+1) * dt
    #   depth = C_pre(s_end) - C_pre(s_start)
    # Falling limb block k (i_peak+1..n_steps-1):
    #   branch offset j = k - (i_peak+1)
    #   depth = C_post((j+1)*dt) - C_post(j*dt)

    t_pre = r * duration_min
    i_peak = int(r * n_steps)
    i_peak = max(0, min(i_peak, n_steps - 1))

    depths: list[float] = [0.0] * n_steps

    # Rising limb
    for k in range(i_peak + 1):
        ta_end = t_pre - k * dt_min
        ta_start = t_pre - (k + 1) * dt_min
        if ta_start < 0.0:
            ta_start = 0.0
        depths[k] = max(0.0, limb_pre(ta_end) - limb_pre(ta_start))

    # Falling limb
    # The last block extends to exactly t_post = (1-r)*duration_min so that
    # sum(depths) == limb_pre(t_pre) + limb_post(t_post) == idf_depth(T) exactly.
    t_post = (1.0 - r) * duration_min
    n_fall = n_steps - i_peak - 1
    for j in range(n_fall):
        k = i_peak + 1 + j
        tb_start = j * dt_min
        if j == n_fall - 1:
            # Last falling block: extend to exact branch end
            tb_end = t_post
        else:
            tb_end = (j + 1) * dt_min
        depths[k] = max(0.0, limb_post(tb_end) - limb_post(tb_start))

    return depths


# ---------------------------------------------------------------------------
# Alternating-block hyetograph
# ---------------------------------------------------------------------------

def alternating_block_hyetograph(
    *,
    idf_table: list[dict[str, float]],
    duration_min: float,
    dt_min: float,
) -> list[float]:
    """Return a list of depth increments (mm per timestep) using the alternating-block method.

    Parameters
    ----------
    idf_table:
        Sorted list of dicts with keys ``duration_min`` and ``intensity_mm_per_hr``.
        Must cover durations from dt_min up to duration_min at dt_min resolution
        (or be interpolable up to that resolution).
    duration_min:
        Total storm duration in minutes.
    dt_min:
        Timestep in minutes.

    Returns
    -------
    list[float]
        Depth increments in mm per timestep, with the largest block at the center.
    """
    n_steps = round(duration_min / dt_min)
    if n_steps < 1:
        raise ValueError(f"duration_min={duration_min} / dt_min={dt_min} must yield >= 1 step")

    # Sort IDF table by duration for interpolation
    sorted_table = sorted(idf_table, key=lambda r: r["duration_min"])
    durations_min = [r["duration_min"] for r in sorted_table]
    intensities = [r["intensity_mm_per_hr"] for r in sorted_table]

    def lookup_intensity(t: float) -> float:
        """Linearly interpolate (or extrapolate at ends) intensity for duration t."""
        if t <= durations_min[0]:
            return intensities[0]
        if t >= durations_min[-1]:
            return intensities[-1]
        for k in range(len(durations_min) - 1):
            if durations_min[k] <= t <= durations_min[k + 1]:
                frac = (t - durations_min[k]) / (durations_min[k + 1] - durations_min[k])
                return intensities[k] + frac * (intensities[k + 1] - intensities[k])
        return intensities[-1]

    # Compute incremental depths: delta_d[k] = D(k*dt) - D((k-1)*dt)
    # where D(t) = i(t) * t / 60  (mm, total depth over t minutes)
    incremental: list[float] = []
    prev_depth = 0.0
    for k in range(1, n_steps + 1):
        t = k * dt_min
        total_depth = lookup_intensity(t) * t / 60.0  # mm
        delta = total_depth - prev_depth
        incremental.append(max(0.0, delta))
        prev_depth = total_depth

    # Sort increments descending to assign to positions
    sorted_increments = sorted(incremental, reverse=True)

    # Alternating-block: place largest at center, alternate left/right
    depths: list[float] = [0.0] * n_steps
    center = n_steps // 2
    left = center - 1
    right = center + 1
    # Place largest at center
    depths[center] = sorted_increments[0]
    toggle = True  # True = place right next, False = place left
    for val in sorted_increments[1:]:
        if toggle and right < n_steps:
            depths[right] = val
            right += 1
            toggle = False
        elif not toggle and left >= 0:
            depths[left] = val
            left -= 1
            toggle = True
        elif right < n_steps:
            depths[right] = val
            right += 1
        elif left >= 0:
            depths[left] = val
            left -= 1

    return depths


# ---------------------------------------------------------------------------
# Output formatting helpers  (match format_rainfall.py contract)
# ---------------------------------------------------------------------------

def _format_number(value: float) -> str:
    """Format a float with up to 6 decimal places, stripping trailing zeros."""
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def build_timeseries_lines(series_name: str, depths_mm: list[float], dt_min: int) -> list[str]:
    """Render SWMM [TIMESERIES] body lines in the same style as format_rainfall.py.

    SWMM expects calendar-date rows:
        <name>  mm/dd/yyyy  HH:MM  value

    We anchor at a synthetic epoch: 01/01/2000.
    The value is depth per timestep in mm (VOLUME format in the gage section).
    The builder reads them as INTENSITY by default, but this script emits
    mm/hr so that the generated gage can use INTENSITY / the same unit as
    format_rainfall.py.

    Wait — format_rainfall.py emits mm/hr (intensity) values.  We must match.
    Convert depth_mm (per dt) back to mm/hr intensity for the TIMESERIES body.
    """
    lines = [";;Name             Date         Time       Value"]
    base_year = 2000
    base_month = 1
    base_day = 1
    total_minutes_offset = 0
    for depth_mm in depths_mm:
        # Convert depth (mm per dt_min) → intensity (mm/hr)
        intensity_mm_hr = (depth_mm / dt_min) * 60.0

        # Compute calendar time from offset
        total_minutes = total_minutes_offset
        days_offset = total_minutes // 1440
        remaining_minutes = total_minutes % 1440
        hh = remaining_minutes // 60
        mm_time = remaining_minutes % 60

        # Simple date arithmetic: roll day count from 01/01/2000
        # Using Gregorian day count for correctness
        _date = _minutes_to_date(base_year, base_month, base_day, total_minutes)
        date_str = f"{_date[1]:02d}/{_date[2]:02d}/{_date[0]}"
        time_str = f"{_date[3]:02d}:{_date[4]:02d}"
        lines.append(
            f"{series_name:<18} {date_str} {time_str} {_format_number(intensity_mm_hr)}"
        )
        total_minutes_offset += dt_min
    return lines


def _minutes_to_date(
    base_year: int, base_month: int, base_day: int, total_minutes: int
) -> tuple[int, int, int, int, int]:
    """Return (year, month, day, hour, minute) adding total_minutes to the base date."""
    days_add = total_minutes // 1440
    remaining = total_minutes % 1440
    hour = remaining // 60
    minute = remaining % 60

    # Day arithmetic using a simple Julian-day approach
    def is_leap(y: int) -> bool:
        return (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)

    def days_in_month(y: int, m: int) -> int:
        dom = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        if m == 2 and is_leap(y):
            return 29
        return dom[m]

    year = base_year
    month = base_month
    day = base_day + days_add

    # Normalise day overflow
    while True:
        dim = days_in_month(year, month)
        if day <= dim:
            break
        day -= dim
        month += 1
        if month > 12:
            month = 1
            year += 1

    return year, month, day, hour, minute


def build_out_json(
    *,
    ok: bool,
    out_json: str,
    out_timeseries: str,
    series_name: str,
    rows: int,
    interval_minutes: int,
    method: str,
    return_period_yr: float,
    coefficients: dict[str, float],
    form: str,
    duration_min: float,
    dt_min: float,
    r: float | None,
) -> dict[str, Any]:
    """Construct the metadata JSON matching format_rainfall.py's stdout contract.

    Mandatory keys (superset of format_rainfall.py's stdout):
      ok, out_json, out_timeseries, series_name, series_names, rows,
      stations, interval_minutes
    Plus new design-storm-specific keys:
      method, return_period_yr, coefficients, form, duration_min, dt_min, r
    """
    return {
        "ok": ok,
        "skill": "swmm-climate",
        "method": method,
        "form": form,
        "return_period_yr": return_period_yr,
        "coefficients": dict(coefficients),
        "duration_min": duration_min,
        "dt_min": dt_min,
        "r": r,
        "series_name": series_name,
        "series_names": [series_name],
        "rows": rows,
        "stations": 1,
        "interval_minutes": interval_minutes,
        "range": {
            "start": "2000-01-01T00:00",
            "end": None,
            "interval_minutes": interval_minutes,
        },
        "outputs": {
            "timeseries_text": out_timeseries,
        },
        "out_json": out_json,
        "out_timeseries": out_timeseries,
    }


# ---------------------------------------------------------------------------
# IDF table loading helpers
# ---------------------------------------------------------------------------

def load_idf_table_csv(path: Path) -> list[dict[str, float]]:
    """Load IDF table from CSV file with columns duration_min,intensity_mm_per_hr."""
    rows: list[dict[str, float]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=2):
            try:
                dur = float(row["duration_min"])
                intensity = float(row["intensity_mm_per_hr"])
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"IDF CSV row {idx}: expected columns 'duration_min' and 'intensity_mm_per_hr'. "
                    f"Error: {exc}"
                ) from exc
            rows.append({"duration_min": dur, "intensity_mm_per_hr": intensity})
    if not rows:
        raise ValueError(f"IDF table CSV is empty: {path}")
    return rows


def load_idf_table_json(raw: str) -> list[dict[str, float]]:
    """Load IDF table from inline JSON string (list of {duration_min, intensity_mm_per_hr})."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"IDF table JSON parse error: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("IDF table JSON must be a list of objects")
    rows: list[dict[str, float]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"IDF table JSON entry {idx} must be an object")
        try:
            dur = float(item["duration_min"])
            intensity = float(item["intensity_mm_per_hr"])
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"IDF table JSON entry {idx}: expected 'duration_min' and 'intensity_mm_per_hr'. "
                f"Error: {exc}"
            ) from exc
        rows.append({"duration_min": dur, "intensity_mm_per_hr": intensity})
    if not rows:
        raise ValueError("IDF table JSON list is empty")
    return rows


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_cn_coefficients(args: argparse.Namespace) -> dict[str, float]:
    required = ["A1", "C", "b", "n"]
    missing = [k for k in required if getattr(args, k.lower(), None) is None]
    if missing:
        raise ValueError(f"CN form requires --A1, --C, --b, --n. Missing: {missing}")
    return {
        "A1": float(args.a1),
        "C": float(args.c),
        "b": float(args.b),
        "n": float(args.n),
    }


def _build_generic_coefficients(args: argparse.Namespace) -> dict[str, float]:
    required_attrs = [("a_coeff", "--a"), ("b", "--b"), ("c", "--c")]
    missing = []
    for attr, flag in required_attrs:
        if getattr(args, attr, None) is None:
            missing.append(flag)
    if missing:
        raise ValueError(f"generic form requires --a, --b, --c. Missing: {missing}")
    return {
        "a": float(args.a_coeff),
        "b": float(args.b),
        "c": float(args.c),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Generate a synthetic design-storm hyetograph (Chicago or alternating-block) "
            "and write SWMM-compatible outputs matching format_rainfall.py's contract."
        )
    )

    # --- Method selection ---
    ap.add_argument(
        "--method",
        choices=["chicago", "alternating_block"],
        required=True,
        help="Hyetograph method: 'chicago' (Keifer-Chu) or 'alternating_block'.",
    )

    # --- IDF form (chicago only) ---
    ap.add_argument(
        "--form",
        choices=["CN", "generic"],
        default="generic",
        help=(
            "IDF formula form for --method chicago. "
            "'CN': q=167·A1·(1+C·lgP)/(t+b)^n [L/s/ha]; "
            "'generic': i=a/(t+b)^c [mm/hr]."
        ),
    )

    # --- Coefficient arguments ---
    ap.add_argument("--a1", type=float, default=None, help="CN form: coefficient A1.")
    ap.add_argument("--C", dest="c", type=float, default=None,
                    help="CN form: coefficient C. (lower-case dest avoids conflict)")
    ap.add_argument("--a-coeff", dest="a_coeff", type=float, default=None,
                    help="generic form: coefficient a.")
    ap.add_argument("--b", type=float, default=None, help="Both forms: coefficient b (time offset, min).")
    ap.add_argument("--n", type=float, default=None, help="CN form: exponent n.")
    # For generic form 'c' exponent - but --C is already used for CN 'C', so use a different name
    ap.add_argument("--c-exp", dest="c_exp", type=float, default=None,
                    help="generic form: exponent c in i=a/(t+b)^c.")

    # --- Alternating-block IDF table ---
    ap.add_argument(
        "--idf-csv",
        type=Path,
        default=None,
        help="CSV file with columns 'duration_min,intensity_mm_per_hr' for alternating-block method.",
    )
    ap.add_argument(
        "--idf-json",
        type=str,
        default=None,
        help=(
            "Inline JSON string (list of {duration_min, intensity_mm_per_hr}) "
            "for alternating-block method. Alternative to --idf-csv."
        ),
    )

    # --- Storm parameters ---
    ap.add_argument(
        "--return-period",
        type=float,
        default=2.0,
        help="Return period in years (default: 2).",
    )
    ap.add_argument(
        "--duration",
        type=float,
        required=True,
        help="Storm duration in minutes.",
    )
    ap.add_argument(
        "--dt",
        type=float,
        default=5.0,
        help="Timestep in minutes (default: 5).",
    )
    ap.add_argument(
        "--r",
        type=float,
        default=0.4,
        help="Peak-position ratio for Chicago method (default: 0.4). Ignored for alternating-block.",
    )

    # --- Output ---
    ap.add_argument("--out-json", type=Path, required=True, help="Output metadata JSON path.")
    ap.add_argument(
        "--out-timeseries", type=Path, required=True,
        help="Output text path for SWMM [TIMESERIES] body."
    )
    ap.add_argument(
        "--series-name",
        default=None,
        help=(
            "Series name token (default: TS_DESIGN_P<P>Y_<duration>MIN). "
            "Must not contain whitespace."
        ),
    )

    args = ap.parse_args(argv)

    # --- Validate duration / dt ---
    if args.duration <= 0:
        raise ValueError("--duration must be > 0")
    if args.dt <= 0:
        raise ValueError("--dt must be > 0")
    if args.return_period <= 0:
        raise ValueError("--return-period must be > 0")
    if not (0.0 < args.r < 1.0):
        raise ValueError("--r must be strictly between 0 and 1")

    n_steps = round(args.duration / args.dt)
    if n_steps < 1:
        raise ValueError(f"duration ({args.duration} min) / dt ({args.dt} min) < 1 step")

    # --- Derive series name ---
    P_str = f"{int(args.return_period)}" if args.return_period == int(args.return_period) else f"{args.return_period}"
    D_str = f"{int(args.duration)}" if args.duration == int(args.duration) else f"{args.duration}"
    default_series_name = f"TS_DESIGN_P{P_str}Y_{D_str}MIN"
    series_name = args.series_name if args.series_name is not None else default_series_name

    # --- Compute depths ---
    if args.method == "chicago":
        form = args.form.upper()
        if form == "CN":
            coefficients = _build_cn_coefficients(args)
        else:
            # Generic form: c exponent from --c-exp
            if args.c_exp is None:
                raise ValueError("generic form requires --c-exp (exponent c in i=a/(t+b)^c)")
            raw_coeff = {"a": args.a_coeff, "b": args.b, "c": args.c_exp}
            missing_g = [k for k, v in raw_coeff.items() if v is None]
            if missing_g:
                flag_map = {"a": "--a-coeff", "b": "--b", "c": "--c-exp"}
                raise ValueError(
                    f"generic form requires --a-coeff, --b, --c-exp. Missing: {[flag_map[k] for k in missing_g]}"
                )
            coefficients = {"a": float(args.a_coeff), "b": float(args.b), "c": float(args.c_exp)}

        depths = chicago_hyetograph(
            coefficients=coefficients,
            form=args.form,
            return_period_yr=args.return_period,
            duration_min=args.duration,
            dt_min=args.dt,
            r=args.r,
        )
    else:
        # Alternating block
        if args.idf_csv is not None and args.idf_json is not None:
            raise ValueError("Provide either --idf-csv or --idf-json, not both.")
        if args.idf_csv is not None:
            idf_table = load_idf_table_csv(args.idf_csv)
        elif args.idf_json is not None:
            idf_table = load_idf_table_json(args.idf_json)
        else:
            raise ValueError("--method alternating_block requires --idf-csv or --idf-json.")

        depths = alternating_block_hyetograph(
            idf_table=idf_table,
            duration_min=args.duration,
            dt_min=args.dt,
        )
        coefficients = {}
        form = "table"

    # --- Build timeseries text ---
    dt_int = int(round(args.dt))
    ts_lines = build_timeseries_lines(series_name, depths, dt_int)
    timeseries_text = "\n".join(ts_lines) + "\n"

    # --- Build out-json ---
    out_payload = build_out_json(
        ok=True,
        out_json=str(args.out_json),
        out_timeseries=str(args.out_timeseries),
        series_name=series_name,
        rows=n_steps,
        interval_minutes=dt_int,
        method=args.method,
        return_period_yr=args.return_period,
        coefficients=coefficients,
        form=args.form if args.method == "chicago" else "table",
        duration_min=args.duration,
        dt_min=args.dt,
        r=args.r if args.method == "chicago" else None,
    )

    # --- Write outputs ---
    write_text(args.out_timeseries, timeseries_text)
    write_json(args.out_json, out_payload)

    # stdout: compact summary matching format_rainfall.py's stdout shape
    print(
        json.dumps(
            {
                "ok": True,
                "out_json": str(args.out_json),
                "out_timeseries": str(args.out_timeseries),
                "series_name": series_name,
                "series_names": [series_name],
                "rows": n_steps,
                "stations": 1,
                "interval_minutes": dt_int,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
