#!/usr/bin/env python3
"""Rainfall ensemble generator (issue #51, slice 5).

Two methods, one CLI:

  Method A — `perturbation`
      Take one observed rainfall timeseries and synthesise N noisy
      realisations from it. Four noise models are supported:
          * gaussian_iid          — additive zero-mean Gaussian
          * multiplicative        — log-normal-style scalar(s) * pattern
          * autocorrelated        — AR(1) noise with configurable phi
          * intensity_scaling     — sigma proportional to intensity (peaks
                                    vary more than troughs)
      Optional `preserve_total_volume=True` rescales each realisation so
      that the integrated rainfall matches the observed total.

  Method B — `idf`
      Sample IDF parameters `(a, b, c)` from their confidence intervals
      and synthesise a design hyetograph for each draw. Three storm types
      are supported:
          * chicago    — Keifer-Chu (1957)
          * huff       — 4 quartiles, Huff (1967), default 1st quartile
          * scs_type_ii — SCS 24-hr Type II canonical mass curve

Outputs:
  * `runs/<case>/09_audit/rainfall_realisations/realisation_<NNN>.csv`
  * `runs/<case>/09_audit/rainfall_ensemble_summary.json`

If a `--base-inp` + `--patch-rainfall-series` pair is supplied, each
realisation is patched into the [TIMESERIES] block of a copy of the base
INP and run through swmm5; peak flow / total volume are aggregated in the
summary.

The Python entry points are also exposed as importable functions so unit
tests can exercise them without invoking swmm5.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RainfallSeries:
    """A rainfall timeseries with timestamps and values (mm/hr)."""

    timestamps: list[datetime]
    values: np.ndarray

    @property
    def interval_minutes(self) -> int:
        if len(self.timestamps) < 2:
            return 0
        deltas = [
            int((self.timestamps[i + 1] - self.timestamps[i]).total_seconds() / 60.0)
            for i in range(len(self.timestamps) - 1)
        ]
        if not deltas:
            return 0
        # Use the modal delta
        return max(set(deltas), key=deltas.count)


# ---------------------------------------------------------------------------
# Rainfall series IO — CSV + SWMM .dat
# ---------------------------------------------------------------------------


_CSV_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
)


def _parse_timestamp(value: str) -> datetime:
    s = value.strip()
    for fmt in _CSV_TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"unsupported timestamp format: '{value}'")


def read_rainfall_series(path: Path) -> RainfallSeries:
    """Read a rainfall timeseries from CSV or SWMM .dat.

    CSV: header row with `timestamp` (or `time`/`date`) and a rainfall
    column. Any column that is not the timestamp is taken as the value.

    SWMM .dat: SWMM external rainfall file with columns
        gauge year month day hour minute value
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        return _read_csv(p)
    if suffix in {".dat", ".txt"}:
        # try CSV first if it looks like a header
        sample = p.read_text(encoding="utf-8", errors="ignore").splitlines()[:3]
        if sample and ("," in sample[0] or "timestamp" in sample[0].lower()):
            return _read_csv(p)
        return _read_swmm_dat(p)
    # default: try CSV
    return _read_csv(p)


def _read_csv(path: Path) -> RainfallSeries:
    timestamps: list[datetime] = []
    values: list[float] = []
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        raise ValueError(f"empty rainfall CSV: {path}")
    header = [c.strip() for c in rows[0]]
    lower = [c.lower() for c in header]
    ts_idx = next(
        (i for i, c in enumerate(lower) if c in {"timestamp", "time", "date", "datetime"}),
        None,
    )
    if ts_idx is None:
        # assume column 0 is timestamp, column 1 is value
        ts_idx, val_idx = 0, 1
    else:
        # value column is the first non-timestamp numeric-looking column
        val_idx = next(
            (i for i in range(len(header)) if i != ts_idx),
            1,
        )
    for row in rows[1:]:
        if not row or all(not c.strip() for c in row):
            continue
        try:
            ts = _parse_timestamp(row[ts_idx])
        except (IndexError, ValueError) as exc:
            raise ValueError(f"row {row}: cannot parse timestamp ({exc})") from exc
        try:
            val = float(row[val_idx])
        except (IndexError, ValueError) as exc:
            raise ValueError(f"row {row}: cannot parse value ({exc})") from exc
        timestamps.append(ts)
        values.append(val)
    return RainfallSeries(timestamps=timestamps, values=np.array(values, dtype=float))


def _read_swmm_dat(path: Path) -> RainfallSeries:
    timestamps: list[datetime] = []
    values: list[float] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith(";"):
                continue
            parts = line.split()
            if len(parts) < 7:
                continue
            try:
                year, month, day, hour, minute = (int(x) for x in parts[1:6])
                val = float(parts[6])
            except ValueError:
                continue
            timestamps.append(datetime(year, month, day, hour, minute))
            values.append(val)
    if not timestamps:
        raise ValueError(f"empty or malformed SWMM .dat: {path}")
    return RainfallSeries(timestamps=timestamps, values=np.array(values, dtype=float))


def write_rainfall_csv(path: Path, series: RainfallSeries) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "rainfall_mm_per_hr"])
        for ts, val in zip(series.timestamps, series.values):
            writer.writerow([ts.strftime("%Y-%m-%d %H:%M:%S"), float(val)])


# ---------------------------------------------------------------------------
# Method A — Perturbation
# ---------------------------------------------------------------------------


PERTURBATION_MODELS = ("gaussian_iid", "multiplicative", "autocorrelated", "intensity_scaling")


def _ensure_rng(rng: np.random.Generator | int | None) -> np.random.Generator:
    if rng is None:
        return np.random.default_rng()
    if isinstance(rng, np.random.Generator):
        return rng
    return np.random.default_rng(int(rng))


def perturb_series(
    *,
    observed: Sequence[float] | np.ndarray,
    config: dict[str, Any],
    n_realisations: int,
    rng: np.random.Generator | int | None = None,
) -> np.ndarray:
    """Return a `(n_realisations, len(observed))` matrix of perturbed series.

    `config` keys:
        model: one of PERTURBATION_MODELS
        sigma: noise scale
        ar1_coefficient: only for `autocorrelated`
        preserve_total_volume: bool (default False)

    All realisations are clipped at zero — rainfall is non-negative.
    If `preserve_total_volume=True`, each realisation is rescaled so its
    sum matches the observed total (when the observed total is positive).
    """
    rng = _ensure_rng(rng)
    if n_realisations <= 0:
        raise ValueError("n_realisations must be >= 1")
    observed = np.asarray(observed, dtype=float)
    if observed.ndim != 1:
        raise ValueError("observed must be a 1-D series")

    model = str(config.get("model", "")).lower()
    if model not in PERTURBATION_MODELS:
        raise ValueError(
            f"unknown perturbation model '{config.get('model')}'. "
            f"Supported: {', '.join(PERTURBATION_MODELS)}"
        )
    sigma = float(config.get("sigma", 0.1))
    preserve_total = bool(config.get("preserve_total_volume", False))

    n = len(observed)
    realisations = np.empty((n_realisations, n), dtype=float)

    if model == "gaussian_iid":
        noise = rng.normal(loc=0.0, scale=sigma, size=(n_realisations, n))
        realisations[:] = observed + noise

    elif model == "multiplicative":
        # log-normal multipliers per timestep, mean 1, std ~ sigma
        # use log-normal with mu = -sigma^2/2 so that E[exp(noise)] = 1
        log_mean = -0.5 * (sigma ** 2)
        log_noise = rng.normal(loc=log_mean, scale=sigma, size=(n_realisations, n))
        multipliers = np.exp(log_noise)
        realisations[:] = observed * multipliers

    elif model == "autocorrelated":
        phi = float(config.get("ar1_coefficient", 0.5))
        if not -1.0 < phi < 1.0:
            raise ValueError("ar1_coefficient must be in (-1, 1)")
        # innovation variance to keep stationary AR(1) variance = sigma^2
        innov_std = sigma * math.sqrt(1.0 - phi ** 2)
        innov = rng.normal(loc=0.0, scale=innov_std, size=(n_realisations, n))
        # initialise with stationary sigma
        noise = np.empty_like(innov)
        noise[:, 0] = rng.normal(loc=0.0, scale=sigma, size=n_realisations)
        for t in range(1, n):
            noise[:, t] = phi * noise[:, t - 1] + innov[:, t]
        realisations[:] = observed + noise

    elif model == "intensity_scaling":
        # variance proportional to intensity ^ 2 (relative noise)
        # noise_t ~ Normal(0, sigma * intensity_t)
        scales = sigma * observed
        # Avoid zero everywhere if observed has zeros — keep a small floor
        # so that low-intensity steps don't have literally zero spread but
        # the peaks still dominate.
        floor = sigma * max(float(observed.max()), 1e-9) * 0.01
        scales = np.maximum(scales, floor)
        noise = rng.normal(loc=0.0, scale=1.0, size=(n_realisations, n)) * scales
        realisations[:] = observed + noise

    # Non-negativity floor
    realisations = np.maximum(realisations, 0.0)

    if preserve_total:
        observed_total = float(observed.sum())
        if observed_total > 0.0:
            row_totals = realisations.sum(axis=1, keepdims=True)
            # rescale rows that have a non-zero sum
            safe = np.where(row_totals > 0.0, row_totals, 1.0)
            realisations = realisations * (observed_total / safe)

    return realisations


# ---------------------------------------------------------------------------
# Method B — IDF design storm hyetographs
# ---------------------------------------------------------------------------


DESIGN_STORM_TYPES = ("chicago", "huff", "scs_type_ii")


def idf_intensity_mm_per_hr(duration_minutes: float, a: float, b: float, c: float) -> float:
    """IDF curve i = a / (duration_hours + b)^c, returns mm/hr.

    Duration is in minutes -> we use hours for the canonical IDF form so that
    `a` has units of mm/hr.
    """
    d_hr = max(duration_minutes / 60.0, 1e-9)
    return float(a / ((d_hr + b) ** c))


def synthesise_design_hyetograph(
    *,
    storm_type: str,
    duration_minutes: int,
    interval_minutes: int,
    a: float,
    b: float,
    c: float,
    huff_quartile: int = 1,
    chicago_peak_position: float = 0.4,
    start_time: datetime | None = None,
) -> RainfallSeries:
    """Build a hyetograph for the given design storm type.

    All hyetographs are returned in mm/hr at `interval_minutes` resolution.
    """
    storm_type = storm_type.lower()
    if storm_type not in DESIGN_STORM_TYPES:
        raise ValueError(
            f"unsupported storm_type '{storm_type}'. Supported: {', '.join(DESIGN_STORM_TYPES)}"
        )
    if duration_minutes <= 0 or interval_minutes <= 0:
        raise ValueError("duration and interval must be positive")
    if duration_minutes % interval_minutes != 0:
        raise ValueError("duration_minutes must be a multiple of interval_minutes")

    n_steps = duration_minutes // interval_minutes
    # Reference total rainfall depth (mm) implied by the IDF curve for the
    # full design duration.
    total_intensity = idf_intensity_mm_per_hr(duration_minutes, a, b, c)
    total_depth_mm = total_intensity * (duration_minutes / 60.0)

    if storm_type == "chicago":
        intensities = _chicago_hyetograph(
            n_steps=n_steps,
            interval_minutes=interval_minutes,
            a=a,
            b=b,
            c=c,
            peak_position=chicago_peak_position,
        )
    elif storm_type == "huff":
        intensities = _huff_hyetograph(
            n_steps=n_steps,
            interval_minutes=interval_minutes,
            total_depth_mm=total_depth_mm,
            quartile=huff_quartile,
        )
    else:  # scs_type_ii
        intensities = _scs_type_ii_hyetograph(
            n_steps=n_steps,
            interval_minutes=interval_minutes,
            total_depth_mm=total_depth_mm,
        )

    # Build timestamps
    t0 = start_time or datetime(2024, 1, 1, 0, 0, 0)
    timestamps = [t0 + timedelta(minutes=interval_minutes * i) for i in range(n_steps)]
    return RainfallSeries(timestamps=timestamps, values=intensities)


def _chicago_hyetograph(
    *,
    n_steps: int,
    interval_minutes: int,
    a: float,
    b: float,
    c: float,
    peak_position: float,
) -> np.ndarray:
    """Keifer-Chu Chicago hyetograph (1957).

    Average intensity over a duration `d` from the IDF curve:
        i_avg(d) = a / (d_hr + b)^c
    Total depth for duration d:  P(d) = i_avg(d) * d_hr
    Average intensity left of peak (over a duration of t_b):
        i_left = a*( (1-c) * t_b/r + b ) / ( t_b/r + b )^(c+1)
    Similarly right of peak.

    Here we use the canonical instantaneous intensity formula (derivative
    of P with respect to duration):
        i(d) = a * ( (1-c)*d_hr + b ) / (d_hr + b)^(c+1)
    placed symmetrically around the peak at fraction `peak_position`.
    """
    if not 0.0 < peak_position < 1.0:
        raise ValueError("chicago peak_position must be in (0, 1)")
    duration_hr = (n_steps * interval_minutes) / 60.0
    t_peak_hr = peak_position * duration_hr
    intensities = np.zeros(n_steps, dtype=float)
    for i in range(n_steps):
        # midpoint of bin i in hours
        t_mid_hr = ((i + 0.5) * interval_minutes) / 60.0
        # offset from the peak in hours (positive)
        dt = abs(t_mid_hr - t_peak_hr)
        # Convert offset to duration relative to the appropriate side
        if t_mid_hr < t_peak_hr:
            d_eff = dt / peak_position
        else:
            d_eff = dt / (1.0 - peak_position)
        d_eff = max(d_eff, 1e-6)
        intensities[i] = a * ((1.0 - c) * d_eff + b) / ((d_eff + b) ** (c + 1.0))
    return intensities


# Huff cumulative distributions (fraction of total rainfall vs fraction of
# total time) for each quartile, from Huff (1967). 11 points (0..1 in 0.1
# steps) is enough resolution for the bilinear interpolation we do below.
_HUFF_CUMULATIVE = {
    1: (0.000, 0.063, 0.178, 0.333, 0.500, 0.620, 0.705, 0.760, 0.838, 0.920, 1.000),
    2: (0.000, 0.040, 0.120, 0.230, 0.420, 0.580, 0.720, 0.820, 0.890, 0.950, 1.000),
    3: (0.000, 0.030, 0.080, 0.160, 0.250, 0.380, 0.540, 0.700, 0.850, 0.950, 1.000),
    4: (0.000, 0.020, 0.060, 0.110, 0.180, 0.270, 0.380, 0.520, 0.680, 0.840, 1.000),
}


def _huff_hyetograph(
    *,
    n_steps: int,
    interval_minutes: int,
    total_depth_mm: float,
    quartile: int,
) -> np.ndarray:
    if quartile not in _HUFF_CUMULATIVE:
        raise ValueError(f"huff_quartile must be one of {sorted(_HUFF_CUMULATIVE)}")
    cum = np.array(_HUFF_CUMULATIVE[quartile], dtype=float)
    # Sample cumulative fraction at each step's right edge
    fractions = np.linspace(0.0, 1.0, n_steps + 1)
    base = np.linspace(0.0, 1.0, len(cum))
    cum_depth = np.interp(fractions, base, cum) * total_depth_mm
    depths_per_step = np.diff(cum_depth)
    # convert depth_mm to mm/hr
    interval_hr = interval_minutes / 60.0
    intensities = depths_per_step / interval_hr
    return intensities


# SCS 24-hr Type II canonical cumulative-rainfall ratios (fraction of total
# rainfall at fraction of total duration). 25 points (every hour for 24 hrs).
_SCS_TYPE_II = (
    0.000, 0.011, 0.022, 0.034, 0.048, 0.063, 0.080, 0.098, 0.120, 0.147,
    0.181, 0.235, 0.663, 0.772, 0.820, 0.850, 0.880, 0.898, 0.916, 0.934,
    0.952, 0.964, 0.976, 0.988, 1.000,
)


def _scs_type_ii_hyetograph(
    *,
    n_steps: int,
    interval_minutes: int,
    total_depth_mm: float,
) -> np.ndarray:
    base_fractions = np.linspace(0.0, 1.0, len(_SCS_TYPE_II))
    cum_ratios = np.array(_SCS_TYPE_II, dtype=float)
    edges = np.linspace(0.0, 1.0, n_steps + 1)
    cum_depth = np.interp(edges, base_fractions, cum_ratios) * total_depth_mm
    depths_per_step = np.diff(cum_depth)
    interval_hr = interval_minutes / 60.0
    intensities = depths_per_step / interval_hr
    return intensities


def sample_idf_param(
    *, value: float, ci: tuple[float, float] | list[float], rng: np.random.Generator
) -> float:
    """Sample an IDF parameter from a normal distribution implied by its CI.

    The 95% CI -> sigma = (upper - lower) / (2 * 1.96).
    """
    lo, hi = float(ci[0]), float(ci[1])
    if hi < lo:
        lo, hi = hi, lo
    sigma = (hi - lo) / (2.0 * 1.959963984540054)
    return float(rng.normal(loc=value, scale=max(sigma, 1e-12)))


def build_idf_realisations(
    *,
    idf_config: dict[str, Any],
    n_realisations: int,
    rng: np.random.Generator | int | None = None,
) -> list[RainfallSeries]:
    """Build N hyetograph realisations by sampling IDF parameters.

    `idf_config` keys (matches issue spec):
        type:              chicago | huff | scs_type_ii
        duration_minutes:  int
        return_period_years: int (metadata)
        interval_minutes:  int (default 5)
        huff_quartile:     int (default 1, only for huff)
        chicago_peak_position: float (default 0.4)
        start_time:        ISO timestamp (optional)
        params:
            a: {value, ci: [lo, hi]}
            b: {value, ci: [lo, hi]}
            c: {value, ci: [lo, hi]}
    """
    rng = _ensure_rng(rng)
    storm_type = str(idf_config["type"]).lower()
    duration = int(idf_config["duration_minutes"])
    interval = int(idf_config.get("interval_minutes", 5))
    huff_quartile = int(idf_config.get("huff_quartile", 1))
    peak_position = float(idf_config.get("chicago_peak_position", 0.4))
    start_iso = idf_config.get("start_time")
    if start_iso:
        start_time = datetime.fromisoformat(str(start_iso))
    else:
        start_time = datetime(2024, 1, 1, 0, 0, 0)
    params = idf_config["params"]
    out: list[RainfallSeries] = []
    for _ in range(n_realisations):
        a = sample_idf_param(value=params["a"]["value"], ci=params["a"]["ci"], rng=rng)
        b = sample_idf_param(value=params["b"]["value"], ci=params["b"]["ci"], rng=rng)
        c = sample_idf_param(value=params["c"]["value"], ci=params["c"]["ci"], rng=rng)
        # Guardrails: IDF requires a > 0 and (d_hr + b) > 0. Clamp.
        a = max(a, 1e-6)
        b = max(b, 0.0)
        c = max(c, 0.01)
        series = synthesise_design_hyetograph(
            storm_type=storm_type,
            duration_minutes=duration,
            interval_minutes=interval,
            a=a,
            b=b,
            c=c,
            huff_quartile=huff_quartile,
            chicago_peak_position=peak_position,
            start_time=start_time,
        )
        out.append(series)
    return out


# ---------------------------------------------------------------------------
# SWMM INP patching for ensemble runs
# ---------------------------------------------------------------------------


_SECTION_RE = re.compile(r"^\s*\[(?P<name>[A-Z_]+)\]\s*$")


def _split_sections(inp_text: str) -> list[tuple[str | None, list[str]]]:
    """Return ordered (section_name | None, lines) groups (preserves blanks)."""
    sections: list[tuple[str | None, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []
    for line in inp_text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            sections.append((current_name, current_lines))
            current_name = m.group("name")
            current_lines = [line]
        else:
            current_lines.append(line)
    sections.append((current_name, current_lines))
    return sections


def patch_rainfall_timeseries(
    *,
    base_inp_text: str,
    series_name: str,
    series: RainfallSeries,
) -> str:
    """Replace `series_name` rows inside the [TIMESERIES] block."""
    sections = _split_sections(base_inp_text)
    out_lines: list[str] = []
    for name, lines in sections:
        if name != "TIMESERIES":
            out_lines.extend(lines)
            continue
        kept: list[str] = []
        for line in lines:
            stripped = line.strip()
            # Keep header / comment lines untouched; drop existing rows
            # for this series.
            if not stripped or stripped.startswith(";") or stripped.startswith("["):
                kept.append(line)
                continue
            parts = stripped.split(None, 1)
            if parts and parts[0] == series_name:
                continue
            kept.append(line)
        # Append the realisation rows
        for ts, val in zip(series.timestamps, series.values):
            kept.append(
                f"{series_name:<18} {ts.strftime('%m/%d/%Y')} {ts.strftime('%H:%M')} {float(val):.6f}"
            )
        out_lines.extend(kept)
    return "\n".join(out_lines) + ("\n" if not base_inp_text.endswith("\n") else "")


def _parse_peak_total_from_rpt(rpt_path: Path, node: str) -> tuple[float | None, float | None]:
    """Extract (peak_flow_cms, total_volume_m3) for `node` from the SWMM .rpt.

    SWMM emits a "Node Inflow Summary" table whose row for `node` contains
    the maximum lateral + total inflow and the volumes. This is the same
    table the existing `swmm_runner.parse_peak_from_rpt` reads.
    """
    if not rpt_path.exists():
        return (None, None)
    text = rpt_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    in_section = False
    peak: float | None = None
    total: float | None = None
    for line in text:
        if "Node Inflow Summary" in line:
            in_section = True
            continue
        if in_section:
            if line.strip().startswith("***") or line.strip().startswith("[") or not line.strip():
                if peak is not None:
                    break
                continue
            parts = line.split()
            # The leading column is the node name; SWMM formats columns
            # consistently as: name type maxLat maxTotal maxFlow time vol1 vol2
            # We extract the maximum total inflow (col 4) and total volume
            # (col 7 or 8, in 10^6 ltr).
            if parts and parts[0] == node:
                try:
                    max_total = float(parts[3])
                    peak = max_total
                except (IndexError, ValueError):
                    pass
                # SWMM "10^6 ltr" volume column — convert to m3
                for tok in parts[5:]:
                    try:
                        total = float(tok) * 1000.0  # 10^6 L -> m^3
                        break
                    except ValueError:
                        continue
                break
    return (peak, total)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _series_to_records(series: RainfallSeries) -> list[dict[str, Any]]:
    return [
        {"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "rainfall_mm_per_hr": float(v)}
        for ts, v in zip(series.timestamps, series.values)
    ]


def _summarise_realisation(series: RainfallSeries) -> dict[str, float]:
    values = np.asarray(series.values, dtype=float)
    interval_hr = max(series.interval_minutes, 1) / 60.0
    return {
        "peak_intensity_mm_per_hr": float(values.max() if values.size else 0.0),
        "total_volume_mm": float(values.sum() * interval_hr),
        "interval_minutes": int(series.interval_minutes),
        "n_steps": int(values.size),
    }


def run_swmm_for_realisation(
    *,
    base_inp_text: str,
    series_name: str,
    series: RainfallSeries,
    realisation_dir: Path,
    swmm_node: str,
) -> dict[str, Any]:
    """Patch + run swmm5 for a single realisation. Returns metrics + status."""
    realisation_dir.mkdir(parents=True, exist_ok=True)
    inp_path = realisation_dir / "model.inp"
    rpt_path = realisation_dir / "model.rpt"
    out_path = realisation_dir / "model.out"
    patched = patch_rainfall_timeseries(
        base_inp_text=base_inp_text,
        series_name=series_name,
        series=series,
    )
    inp_path.write_text(patched, encoding="utf-8")

    if not shutil.which("swmm5"):
        return {
            "status": "skipped",
            "reason": "swmm5 binary not on PATH",
            "files": {"inp": str(inp_path)},
        }

    proc = subprocess.run(
        ["swmm5", str(inp_path), str(rpt_path), str(out_path)],
        capture_output=True,
        text=True,
    )
    (realisation_dir / "stdout.txt").write_text(proc.stdout, encoding="utf-8", errors="ignore")
    (realisation_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8", errors="ignore")
    if proc.returncode != 0:
        return {
            "status": "failed",
            "reason": f"swmm5 returned {proc.returncode}",
            "files": {
                "inp": str(inp_path),
                "rpt": str(rpt_path),
                "stdout": str(realisation_dir / "stdout.txt"),
                "stderr": str(realisation_dir / "stderr.txt"),
            },
        }
    peak, total_vol = _parse_peak_total_from_rpt(rpt_path, swmm_node)
    return {
        "status": "ok",
        "metrics": {
            "peak_flow": peak,
            "total_volume_m3": total_vol,
        },
        "files": {
            "inp": str(inp_path),
            "rpt": str(rpt_path),
            "out": str(out_path),
        },
    }


def aggregate_metrics(values: Iterable[Any]) -> dict[str, Any]:
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return {"count": 0, "min": None, "max": None, "mean": None, "p05": None, "p50": None, "p95": None}
    arr = np.array(nums, dtype=float)
    return {
        "count": len(nums),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
        "p05": float(np.percentile(arr, 5)),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
    }


def generate_realisations(
    *,
    method: str,
    config: dict[str, Any],
    rng: np.random.Generator | int | None = None,
) -> tuple[list[RainfallSeries], dict[str, Any]]:
    """Return (realisations, controls) for the requested method.

    For `perturbation`, the realisations inherit timestamps from the
    observed input. For `idf`, timestamps are synthesised from
    `start_time` + `interval_minutes`.
    """
    rng = _ensure_rng(rng)
    method = method.lower()
    n = int(config.get("n_realisations", 100))
    if method == "perturbation":
        pert_cfg = config["perturbation"]
        path = Path(config["input_rainfall_path"])
        observed = read_rainfall_series(path)
        if observed.values.size == 0:
            raise ValueError(f"observed series is empty: {path}")
        matrix = perturb_series(
            observed=observed.values,
            config=pert_cfg,
            n_realisations=n,
            rng=rng,
        )
        realisations = [
            RainfallSeries(timestamps=list(observed.timestamps), values=row)
            for row in matrix
        ]
        controls = {
            "input_rainfall_path": str(path),
            "interval_minutes": observed.interval_minutes,
            "observed_n_steps": int(observed.values.size),
            "perturbation": pert_cfg,
        }
        return realisations, controls

    if method == "idf":
        idf_cfg = config["idf"]
        # honour `n_realisations` at the top level as the spec says, but
        # also accept it inside `idf` (the config example in the issue
        # places it under `idf`).
        n_real = int(config.get("n_realisations", idf_cfg.get("n_realisations", n)))
        realisations = build_idf_realisations(
            idf_config=idf_cfg,
            n_realisations=n_real,
            rng=rng,
        )
        controls = {
            "idf": idf_cfg,
        }
        return realisations, controls

    raise ValueError(f"unknown method '{method}' (expected: perturbation | idf)")


def run_ensemble(
    *,
    method: str,
    config: dict[str, Any],
    run_root: Path,
    base_inp: Path | None,
    series_name: str,
    swmm_node: str,
    seed: int,
    dry_run: bool,
) -> dict[str, Any]:
    """Top-level orchestration. Writes realisation CSVs + summary JSON."""
    started_at = utc_now()
    rng = np.random.default_rng(int(seed))
    audit_dir = run_root / "09_audit"
    realisations_dir = audit_dir / "rainfall_realisations"
    audit_dir.mkdir(parents=True, exist_ok=True)
    realisations_dir.mkdir(parents=True, exist_ok=True)

    realisations, controls = generate_realisations(method=method, config=config, rng=rng)

    # Persist each realisation as CSV + summarise
    per_realisation: list[dict[str, Any]] = []
    base_inp_text: str | None = None
    if base_inp is not None and not dry_run:
        base_inp_text = Path(base_inp).read_text(encoding="utf-8", errors="ignore")

    n_digits = max(len(str(len(realisations) - 1)), 3)
    for idx, series in enumerate(realisations):
        rel_name = f"realisation_{idx:0{n_digits}d}"
        csv_path = realisations_dir / f"{rel_name}.csv"
        write_rainfall_csv(csv_path, series)
        rec: dict[str, Any] = {
            "index": idx,
            "name": rel_name,
            "csv": str(csv_path),
            "summary": _summarise_realisation(series),
            "status": "csv_written",
        }
        if base_inp_text is not None:
            run_dir = audit_dir / "swmm_realisations" / rel_name
            swmm_result = run_swmm_for_realisation(
                base_inp_text=base_inp_text,
                series_name=series_name,
                series=series,
                realisation_dir=run_dir,
                swmm_node=swmm_node,
            )
            rec["swmm"] = swmm_result
            rec["status"] = swmm_result.get("status", "unknown")
        per_realisation.append(rec)

    swmm_metrics_present = [
        (r.get("swmm") or {}).get("metrics", {}) for r in per_realisation if r.get("swmm")
    ]
    payload: dict[str, Any] = {
        "schema": "swmm-uncertainty/rainfall-ensemble/v1",
        "method": method,
        "created_at_utc": started_at,
        "finished_at_utc": utc_now(),
        "seed": int(seed),
        "n_realisations": len(realisations),
        "controls": controls,
        "outputs": {
            "realisations_dir": str(realisations_dir),
            "summary_json": str(audit_dir / "rainfall_ensemble_summary.json"),
        },
        "rainfall_ensemble_stats": {
            "peak_intensity_mm_per_hr": aggregate_metrics(
                r["summary"]["peak_intensity_mm_per_hr"] for r in per_realisation
            ),
            "total_volume_mm": aggregate_metrics(
                r["summary"]["total_volume_mm"] for r in per_realisation
            ),
        },
        "swmm_ensemble_stats": {
            "peak_flow": aggregate_metrics(m.get("peak_flow") for m in swmm_metrics_present),
            "total_volume_m3": aggregate_metrics(m.get("total_volume_m3") for m in swmm_metrics_present),
        },
        "realisations": per_realisation,
    }
    write_json(audit_dir / "rainfall_ensemble_summary.json", payload)
    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a rainfall ensemble via perturbation of an observed series or IDF-based design storms.",
    )
    p.add_argument("--method", required=True, choices=("perturbation", "idf"))
    p.add_argument("--config", required=True, type=Path, help="JSON config (see SKILL.md).")
    p.add_argument("--run-root", required=True, type=Path)
    p.add_argument("--base-inp", type=Path, default=None, help="If given, each realisation is patched + run through swmm5.")
    p.add_argument("--series-name", default="TS_RAIN", help="Name of the SWMM [TIMESERIES] block to replace.")
    p.add_argument("--swmm-node", default="O1", help="Node to extract peak flow / total volume from.")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--dry-run", action="store_true", help="Generate realisations + CSVs but skip swmm5.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_json(args.config)
    if not isinstance(config, dict):
        raise SystemExit("config JSON must be an object")

    # Allow --method to override config["method"] (and vice versa)
    method = str(args.method or config.get("method", "")).lower()

    payload = run_ensemble(
        method=method,
        config=config,
        run_root=Path(args.run_root),
        base_inp=args.base_inp,
        series_name=str(args.series_name),
        swmm_node=str(args.swmm_node),
        seed=int(args.seed),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps({
        "method": payload["method"],
        "n_realisations": payload["n_realisations"],
        "summary_json": payload["outputs"]["summary_json"],
        "rainfall_ensemble_stats": payload["rainfall_ensemble_stats"],
        "swmm_ensemble_stats": payload["swmm_ensemble_stats"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
