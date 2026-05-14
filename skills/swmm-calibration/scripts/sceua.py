#!/usr/bin/env python3
"""SCE-UA (Shuffled Complex Evolution) calibration strategy for SWMM.

Wraps ``spotpy.algorithms.sceua`` around the existing patch-and-run pipeline
used by ``swmm_calibrate.py``. The primary objective is KGE (Kling-Gupta
efficiency); spotpy minimises ``(1 - KGE)``.

Outputs:
  * ``calibration_summary.json`` — shape locked-in by tests/test_calibration_summary_schema.py
  * ``best_params.json`` — JSON object of the best-found parameter values
  * ``convergence.csv`` — per-iteration KGE so reviewers can inspect convergence

Why a separate module: keeping spotpy off the import path of the main CLI
means existing strategies (random / lhs / adaptive) still work when spotpy
is not installed; ``swmm_calibrate.py`` imports this module only when the
``--strategy sceua`` branch is taken.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from metrics import align_series, compute_metrics, kge
from inp_patch import patch_inp_text


# ---------------------------------------------------------------------------
# Schema helpers (pure functions — exercised by test_calibration_summary_schema.py)
# ---------------------------------------------------------------------------

PRIMARY_OBJECTIVE_NAME = "kge"
STRATEGY_NAME = "sceua"

REQUIRED_SECONDARY_KEYS = (
    "nse",
    "pbias_pct",
    "rmse",
    "peak_error_rel",
    "peak_timing_min",
)


def build_calibration_summary(
    primary_value: float,
    kge_decomposition: dict[str, float],
    secondary_metrics: dict[str, float | None],
    iterations: int,
    convergence_trace_ref: str,
) -> dict[str, Any]:
    """Return the calibration_summary.json payload shape defined in issue #48."""

    if not isinstance(primary_value, (int, float)) or not math.isfinite(float(primary_value)):
        raise ValueError(f"primary_value must be a finite number, got {primary_value!r}")
    primary_value_f = float(primary_value)

    decomposition_out = {}
    for key in ("r", "alpha", "beta"):
        if key not in kge_decomposition:
            raise ValueError(f"kge_decomposition missing key {key!r}")
        decomposition_out[key] = float(kge_decomposition[key])

    secondary_out: dict[str, float | None] = {}
    for key in REQUIRED_SECONDARY_KEYS:
        if key not in secondary_metrics:
            raise ValueError(f"secondary_metrics missing key {key!r}")
        value = secondary_metrics[key]
        if value is None:
            secondary_out[key] = None
        else:
            secondary_out[key] = float(value)

    if not isinstance(iterations, int) or iterations < 1:
        raise ValueError(f"iterations must be a positive integer, got {iterations!r}")
    if not isinstance(convergence_trace_ref, str) or not convergence_trace_ref:
        raise ValueError("convergence_trace_ref must be a non-empty string")

    return {
        "primary_objective": PRIMARY_OBJECTIVE_NAME,
        "primary_value": primary_value_f,
        "kge_decomposition": decomposition_out,
        "secondary_metrics": secondary_out,
        "strategy": STRATEGY_NAME,
        "iterations": iterations,
        "convergence_trace_ref": convergence_trace_ref,
    }


def secondary_metrics_from_bundle(metrics_bundle, observed_flow: pd.Series) -> dict[str, float | None]:
    """Pull NSE / PBIAS% / RMSE / peak-flow / peak-timing out of a MetricBundle."""

    nse = metrics_bundle.nse
    rmse = metrics_bundle.rmse
    peak_error_rel = metrics_bundle.peak_flow_error
    peak_timing_min = metrics_bundle.peak_timing_error_minutes
    # PBIAS% = 100 * sum(sim - obs) / sum(obs). Use bias * count / sum(obs).
    obs_sum = float(observed_flow.sum())
    if obs_sum == 0.0 or metrics_bundle.bias is None:
        pbias_pct: float | None = None
    else:
        # bias is mean(sim - obs); total diff = bias * count.
        total_diff = float(metrics_bundle.bias) * float(metrics_bundle.count)
        pbias_pct = float(100.0 * total_diff / obs_sum)
    return {
        "nse": None if nse is None else float(nse),
        "pbias_pct": pbias_pct,
        "rmse": None if rmse is None else float(rmse),
        "peak_error_rel": None if peak_error_rel is None else float(peak_error_rel),
        "peak_timing_min": None if peak_timing_min is None else float(peak_timing_min),
    }


# ---------------------------------------------------------------------------
# spotpy setup wiring
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SceuaConfig:
    base_inp: Path
    patch_map: dict
    observed: pd.DataFrame
    run_root: Path
    swmm_node: str
    swmm_attr: str
    aggregate: str
    obs_start: str | None
    obs_end: str | None
    bounds: dict           # name -> ParamBound (from swmm_calibrate.parse_search_space)
    iterations: int
    seed: int
    ngs: int               # number of complexes
    convergence_csv: Path
    swmm_runner: Callable  # signature: (inp_path) -> (rc, rpt, out_path)
    extract_series: Callable  # signature: (out_path) -> pd.DataFrame[timestamp, flow]


class SwmmSpotSetup:
    """Spotpy setup adapter for the existing SWMM patch-and-run pipeline."""

    def __init__(self, config: SceuaConfig) -> None:
        self.config = config
        # Spotpy expects parameter list via .params attribute or parameters() method.
        import spotpy  # local import keeps non-sceua paths free of the dependency

        self._spotpy = spotpy
        self._param_order = list(config.bounds.keys())
        self._params = [
            spotpy.parameter.Uniform(
                name,
                low=config.bounds[name].min_value,
                high=config.bounds[name].max_value,
                optguess=(config.bounds[name].min_value + config.bounds[name].max_value) / 2.0,
            )
            for name in self._param_order
        ]
        # Observed timestamps cached once so simulation() can align without rereading.
        obs_clean = config.observed.copy()
        obs_clean["timestamp"] = pd.to_datetime(obs_clean["timestamp"])
        self._observed = obs_clean.sort_values("timestamp").reset_index(drop=True)
        # The trace records each rep's primary KGE so we can write convergence.csv.
        self._convergence: list[tuple[int, float]] = []
        self._call_count = 0

    # spotpy hook: return the parameter generator list.
    def parameters(self):
        return self._spotpy.parameter.generate(self._params)

    def _values_to_named(self, values: Sequence[float]) -> dict[str, float | int]:
        out: dict[str, float | int] = {}
        for i, name in enumerate(self._param_order):
            bound = self.config.bounds[name]
            raw = float(values[i])
            if bound.value_type == "int":
                out[name] = int(round(raw))
            elif bound.precision is not None:
                out[name] = round(raw, bound.precision)
            else:
                out[name] = raw
        return out

    def _run_swmm_for_params(self, params: dict[str, float | int]) -> pd.DataFrame | None:
        cfg = self.config
        self._call_count += 1
        trial_dir = cfg.run_root / f"sceua_{self._call_count:04d}"
        trial_dir.mkdir(parents=True, exist_ok=True)
        try:
            patched_text = patch_inp_text(
                cfg.base_inp.read_text(errors="ignore"),
                cfg.patch_map,
                params,
            )
        except Exception:
            return None
        inp = trial_dir / "model.inp"
        inp.write_text(patched_text, encoding="utf-8")
        try:
            rc, _, out_path = cfg.swmm_runner(inp, trial_dir)
        except FileNotFoundError:
            return None
        except Exception:
            return None
        if rc != 0:
            return None
        try:
            sim = cfg.extract_series(out_path)
        except Exception:
            return None
        return sim

    def simulation(self, values) -> np.ndarray:
        params = self._values_to_named(values)
        sim_df = self._run_swmm_for_params(params)
        if sim_df is None or sim_df.empty:
            # Return a series of NaNs aligned to observed length; objectivefunction
            # will collapse this to a large penalty.
            return np.full(len(self._observed), np.nan, dtype=float)
        aligned = align_series(self._observed, sim_df)
        if aligned.empty:
            return np.full(len(self._observed), np.nan, dtype=float)
        # Spotpy expects simulation() and evaluation() to share length.
        # Project the aligned simulated flow onto the full observed length, NaN-padding
        # any observed timestamps without a matching simulation point.
        obs_ts = self._observed["timestamp"].to_numpy()
        sim_map = dict(zip(aligned["timestamp"].to_numpy(), aligned["flow_sim"].astype(float).to_numpy()))
        sim_array = np.array([sim_map.get(ts, np.nan) for ts in obs_ts], dtype=float)
        return sim_array

    def evaluation(self) -> np.ndarray:
        return self._observed["flow"].astype(float).to_numpy()

    def objectivefunction(self, simulation, evaluation, params=None) -> float:
        sim = np.asarray(simulation, dtype=float)
        obs = np.asarray(evaluation, dtype=float)
        mask = np.isfinite(sim) & np.isfinite(obs)
        if mask.sum() < 2:
            self._convergence.append((self._call_count, float("nan")))
            return 1.0e6  # large penalty: minimisation drives this down
        sim_ok = sim[mask]
        obs_ok = obs[mask]
        ts = self._observed["timestamp"].to_numpy()[mask]
        sim_df = pd.DataFrame({"timestamp": ts, "flow": sim_ok})
        obs_df = pd.DataFrame({"timestamp": ts, "flow": obs_ok})
        kge_result = kge(obs_df, sim_df)
        kge_value = kge_result["kge"]
        if kge_value is None or not math.isfinite(kge_value):
            self._convergence.append((self._call_count, float("nan")))
            return 1.0e6
        self._convergence.append((self._call_count, float(kge_value)))
        return float(1.0 - kge_value)  # spotpy sceua minimises this

    # Accessors used by run_sceua after sampling completes.
    @property
    def convergence_trace(self) -> list[tuple[int, float]]:
        return list(self._convergence)

    @property
    def param_order(self) -> list[str]:
        return list(self._param_order)


def write_convergence_csv(trace: list[tuple[int, float]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["iteration", "kge"])
        for iteration, kge_value in trace:
            writer.writerow([iteration, "" if math.isnan(kge_value) else f"{kge_value:.8f}"])


def _best_iteration(trace: list[tuple[int, float]]) -> tuple[int, float] | None:
    valid = [(it, v) for it, v in trace if not math.isnan(v)]
    if not valid:
        return None
    return max(valid, key=lambda x: x[1])


def run_sceua(config: SceuaConfig) -> dict[str, Any]:
    """Run SCE-UA, write convergence.csv, return a results dict with best_params + summary."""

    import spotpy  # noqa: WPS433

    setup = SwmmSpotSetup(config)
    sampler = spotpy.algorithms.sceua(
        setup,
        dbname=str(config.run_root / "sceua_db"),
        dbformat="ram",
        random_state=config.seed,
        save_sim=False,
    )
    sampler.sample(config.iterations, ngs=config.ngs)
    trace = setup.convergence_trace
    write_convergence_csv(trace, config.convergence_csv)

    best = _best_iteration(trace)
    if best is None:
        raise RuntimeError("SCE-UA produced no valid iterations; cannot build summary.")

    best_iter, best_kge = best
    # Re-run SWMM at the best parameters to capture full metrics + decomposition.
    # The trace iteration -> params mapping lives implicitly in the per-call trial
    # directories; we re-fetch using spotpy's results database.
    results = sampler.getdata()
    # Spotpy 1.6 ramfs returns a numpy structured array: 'like1' is the objective
    # (we returned 1 - KGE), 'par<name>' the parameters.
    if results is None or len(results) == 0:
        raise RuntimeError("SCE-UA results database was empty.")

    # Pick the best row by smallest 'like1' (= smallest 1 - KGE, i.e. largest KGE).
    like_field = None
    for candidate in ("like1", "like_kge", "like"):
        if candidate in results.dtype.names:
            like_field = candidate
            break
    if like_field is None:
        # Fall back to first numeric field.
        like_field = results.dtype.names[0]
    best_row_idx = int(np.argmin(results[like_field]))
    best_row = results[best_row_idx]
    best_params: dict[str, float | int] = {}
    for name in setup.param_order:
        col = f"par{name}"
        if col in results.dtype.names:
            value = float(best_row[col])
            bound = config.bounds[name]
            if bound.value_type == "int":
                best_params[name] = int(round(value))
            elif bound.precision is not None:
                best_params[name] = round(value, bound.precision)
            else:
                best_params[name] = value

    # Re-run SWMM with best_params to compute full metrics on the aligned series.
    cfg = config
    final_trial_dir = cfg.run_root / "sceua_best"
    final_trial_dir.mkdir(parents=True, exist_ok=True)
    patched_text = patch_inp_text(
        cfg.base_inp.read_text(errors="ignore"),
        cfg.patch_map,
        best_params,
    )
    final_inp = final_trial_dir / "model.inp"
    final_inp.write_text(patched_text, encoding="utf-8")
    rc, _, out_path = cfg.swmm_runner(final_inp, final_trial_dir)
    if rc != 0:
        raise RuntimeError(f"swmm5 final-best run failed with rc={rc}")
    sim_df = cfg.extract_series(out_path)

    aligned = align_series(setup._observed, sim_df)
    metrics_bundle = compute_metrics(setup._observed, sim_df)
    kge_block = kge(setup._observed, sim_df)
    if kge_block["decomposition"] is None or kge_block["kge"] is None:
        raise RuntimeError("KGE undefined for the best parameter set; cannot summarise.")

    # Observed flow on the aligned overlap, for PBIAS%.
    obs_for_pbias = aligned["flow_obs"].astype(float) if not aligned.empty else setup._observed["flow"].astype(float)
    secondary = secondary_metrics_from_bundle(metrics_bundle, obs_for_pbias)

    summary = build_calibration_summary(
        primary_value=kge_block["kge"],
        kge_decomposition=kge_block["decomposition"],
        secondary_metrics=secondary,
        iterations=config.iterations,
        convergence_trace_ref=config.convergence_csv.name,
    )

    return {
        "summary": summary,
        "best_params": best_params,
        "best_iteration": best_iter,
        "best_kge_from_trace": best_kge,
        "convergence_csv": str(config.convergence_csv),
        "total_calls": setup._call_count,
        "metrics_bundle": metrics_bundle.to_dict(),
    }


if __name__ == "__main__":
    # CLI usage is via swmm_calibrate.py search --strategy sceua; this module is
    # intentionally library-style.
    import sys

    print("sceua.py is a library module; invoke via 'swmm_calibrate.py search --strategy sceua'.", file=sys.stderr)
    sys.exit(0)
