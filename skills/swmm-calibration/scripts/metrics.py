#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

import math
import pandas as pd


@dataclass
class MetricBundle:
    count: int
    nse: float | None
    rmse: float | None
    bias: float | None
    peak_flow_error: float | None
    peak_timing_error_minutes: float | None
    kge: float | None = None
    kge_decomposition: dict | None = None

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "nse": self.nse,
            "rmse": self.rmse,
            "bias": self.bias,
            "peak_flow_error": self.peak_flow_error,
            "peak_timing_error_minutes": self.peak_timing_error_minutes,
            "kge": self.kge,
            "kge_decomposition": self.kge_decomposition,
        }


def align_series(observed: pd.DataFrame, simulated: pd.DataFrame) -> pd.DataFrame:
    obs = observed[["timestamp", "flow"]].copy()
    sim = simulated[["timestamp", "flow"]].copy()
    obs["timestamp"] = pd.to_datetime(obs["timestamp"])
    sim["timestamp"] = pd.to_datetime(sim["timestamp"])
    out = pd.merge(obs, sim, on="timestamp", how="inner", suffixes=("_obs", "_sim"))
    out = out.dropna(subset=["flow_obs", "flow_sim"]).sort_values("timestamp")
    return out.reset_index(drop=True)


def _safe_div(num: float, den: float) -> float | None:
    if den == 0:
        return None
    return num / den


def _kge_components(sim: pd.Series, obs: pd.Series) -> dict | None:
    """Return Kling-Gupta r/alpha/beta decomposition or None if undefined."""

    if len(sim) < 2 or len(obs) < 2:
        return None
    sim_mean = float(sim.mean())
    obs_mean = float(obs.mean())
    if obs_mean == 0.0:
        return None
    sim_std = float(sim.std(ddof=0))
    obs_std = float(obs.std(ddof=0))
    if obs_std == 0.0:
        return None
    # Pearson correlation; if either series has zero variance, corrcoef gives NaN.
    corr_matrix = sim.to_numpy().reshape(-1)
    obs_array = obs.to_numpy().reshape(-1)
    # numpy.corrcoef returns NaN if either input has zero variance.
    import numpy as np  # local import keeps module load cheap

    if sim_std == 0.0:
        # When the simulated series is constant, correlation is undefined.
        r = 0.0
    else:
        r = float(np.corrcoef(corr_matrix, obs_array)[0, 1])
        if not math.isfinite(r):
            r = 0.0
    alpha = sim_std / obs_std
    beta = sim_mean / obs_mean
    return {"r": r, "alpha": alpha, "beta": beta}


def kge(observed: pd.DataFrame, simulated: pd.DataFrame) -> dict:
    """Compute Kling-Gupta efficiency and its (r, alpha, beta) decomposition.

    Reference: Gupta et al. (2009), Decomposition of the mean squared error
    and NSE performance criteria. KGE = 1 - sqrt((r-1)^2 + (alpha-1)^2 + (beta-1)^2)
    where r is Pearson correlation, alpha = std(sim)/std(obs), beta = mean(sim)/mean(obs).
    A perfect simulation has KGE = 1.
    """

    aligned = align_series(observed, simulated)
    if aligned.empty:
        return {"kge": None, "decomposition": None}
    sim = aligned["flow_sim"].astype(float)
    obs = aligned["flow_obs"].astype(float)
    decomposition = _kge_components(sim, obs)
    if decomposition is None:
        return {"kge": None, "decomposition": None}
    r = decomposition["r"]
    alpha = decomposition["alpha"]
    beta = decomposition["beta"]
    kge_value = 1.0 - math.sqrt((r - 1.0) ** 2 + (alpha - 1.0) ** 2 + (beta - 1.0) ** 2)
    return {"kge": float(kge_value), "decomposition": {"r": r, "alpha": alpha, "beta": beta}}


def compute_metrics(observed: pd.DataFrame, simulated: pd.DataFrame) -> MetricBundle:
    aligned = align_series(observed, simulated)
    n = int(len(aligned))
    if n == 0:
        return MetricBundle(0, None, None, None, None, None, None, None)

    obs = aligned["flow_obs"].astype(float)
    sim = aligned["flow_sim"].astype(float)
    diff = sim - obs

    rmse = float(math.sqrt((diff.pow(2).mean())))
    bias = float(diff.mean())

    den = float(((obs - obs.mean()) ** 2).sum())
    nse = None if den == 0 else float(1.0 - ((diff.pow(2).sum()) / den))

    peak_obs_idx = int(obs.idxmax())
    peak_sim_idx = int(sim.idxmax())
    peak_obs = float(obs.iloc[peak_obs_idx])
    peak_sim = float(sim.iloc[peak_sim_idx])
    peak_flow_error = _safe_div(peak_sim - peak_obs, peak_obs)

    t_obs = pd.Timestamp(aligned.loc[peak_obs_idx, "timestamp"])
    t_sim = pd.Timestamp(aligned.loc[peak_sim_idx, "timestamp"])
    peak_timing_error_minutes = float((t_sim - t_obs).total_seconds() / 60.0)

    decomposition = _kge_components(sim, obs)
    if decomposition is None:
        kge_value: float | None = None
    else:
        r = decomposition["r"]
        alpha = decomposition["alpha"]
        beta = decomposition["beta"]
        kge_value = float(1.0 - math.sqrt((r - 1.0) ** 2 + (alpha - 1.0) ** 2 + (beta - 1.0) ** 2))

    return MetricBundle(
        n,
        nse,
        rmse,
        bias,
        peak_flow_error,
        peak_timing_error_minutes,
        kge_value,
        decomposition,
    )


def score_from_metrics(metrics: MetricBundle, objective: str) -> float:
    obj = objective.lower().strip()
    if obj == "nse":
        return float("-inf") if metrics.nse is None else metrics.nse
    if obj == "kge":
        return float("-inf") if metrics.kge is None else metrics.kge
    if obj == "rmse":
        return float("inf") if metrics.rmse is None else -metrics.rmse
    if obj == "bias":
        return float("inf") if metrics.bias is None else -abs(metrics.bias)
    if obj == "peak_flow_error":
        return float("inf") if metrics.peak_flow_error is None else -abs(metrics.peak_flow_error)
    if obj == "peak_timing_error":
        return float("inf") if metrics.peak_timing_error_minutes is None else -abs(metrics.peak_timing_error_minutes)
    raise ValueError(f"Unsupported objective: {objective}")
