#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
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

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "nse": self.nse,
            "rmse": self.rmse,
            "bias": self.bias,
            "peak_flow_error": self.peak_flow_error,
            "peak_timing_error_minutes": self.peak_timing_error_minutes,
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


def compute_metrics(observed: pd.DataFrame, simulated: pd.DataFrame) -> MetricBundle:
    aligned = align_series(observed, simulated)
    n = int(len(aligned))
    if n == 0:
        return MetricBundle(0, None, None, None, None, None)

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

    return MetricBundle(n, nse, rmse, bias, peak_flow_error, peak_timing_error_minutes)


def score_from_metrics(metrics: MetricBundle, objective: str) -> float:
    obj = objective.lower().strip()
    if obj == "nse":
        return float("-inf") if metrics.nse is None else metrics.nse
    if obj == "rmse":
        return float("inf") if metrics.rmse is None else -metrics.rmse
    if obj == "bias":
        return float("inf") if metrics.bias is None else -abs(metrics.bias)
    if obj == "peak_flow_error":
        return float("inf") if metrics.peak_flow_error is None else -abs(metrics.peak_flow_error)
    if obj == "peak_timing_error":
        return float("inf") if metrics.peak_timing_error_minutes is None else -abs(metrics.peak_timing_error_minutes)
    raise ValueError(f"Unsupported objective: {objective}")
