#!/usr/bin/env python3
"""DREAM-ZS (DiffeRential Evolution Adaptive Metropolis) Bayesian calibration for SWMM.

Wraps ``spotpy.algorithms.dream`` around the existing patch-and-run pipeline used
by ``swmm_calibrate.py``. Treats DREAM as a posterior sampler over the same
parameter bounds the SCE-UA wrapper consumes, but reports a posterior summary
in addition to the MAP-estimate ``calibration_summary.json``.

Likelihood (issue #53):

    L(theta) = exp(-0.5 * (1 - KGE(theta)) / sigma^2)

spotpy DREAM minimises ``-log L`` via its ``acceptance_test_option=6`` Metropolis
ratio, so we return the log-likelihood ``-0.5 * (1 - KGE) / sigma^2``. Combined
with a uniform prior implied by the parameter bounds, samples are draws from
the posterior.

Outputs (under the audit dir chosen by the caller, defaults to
``<summary-parent>/`` when not provided explicitly):

  * ``posterior_samples.csv``       — all post-burn-in MCMC samples
  * ``best_params.json``            — MAP (highest-likelihood) parameter set
  * ``chain_convergence.json``      — Gelman-Rubin Rhat per parameter
  * ``posterior_<param>.png``       — per-parameter marginal histogram
  * ``posterior_correlation.png``   — parameter correlation matrix
  * ``calibration_summary.json``    — Slice 1 shape + ``posterior_summary``

Why a separate module: keeping spotpy.algorithms.dream off the import path of
the main CLI means existing strategies still work when spotpy is not installed.
``swmm_calibrate.py`` imports this module only when the ``--strategy dream-zs``
branch is taken.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from metrics import align_series, compute_metrics, kge
from inp_patch import patch_inp_text
from sceua import (
    REQUIRED_SECONDARY_KEYS,
    build_calibration_summary,
    secondary_metrics_from_bundle,
)


STRATEGY_NAME = "dream-zs"
PRIMARY_OBJECTIVE_NAME = "kge"
DEFAULT_SIGMA = 0.1  # likelihood width on (1 - KGE); 0.1 keeps top-decile fits informative
DEFAULT_RHAT_THRESHOLD = 1.2


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DreamZsConfig:
    base_inp: Path
    patch_map: dict
    observed: pd.DataFrame
    run_root: Path
    swmm_node: str
    swmm_attr: str
    aggregate: str
    obs_start: str | None
    obs_end: str | None
    bounds: dict  # name -> ParamBound
    iterations: int
    seed: int
    n_chains: int
    sigma: float
    rhat_threshold: float
    output_dir: Path
    swmm_runner: Callable
    extract_series: Callable
    runs_after_convergence: int = 50


# ---------------------------------------------------------------------------
# Spotpy setup adapter
# ---------------------------------------------------------------------------


class _SwmmDreamSetup:
    """Spotpy setup adapter that returns a Bayesian log-likelihood."""

    def __init__(self, config: DreamZsConfig) -> None:
        self.config = config
        import spotpy  # local import keeps non-dream paths free of the dependency

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
        obs_clean = config.observed.copy()
        obs_clean["timestamp"] = pd.to_datetime(obs_clean["timestamp"])
        self._observed = obs_clean.sort_values("timestamp").reset_index(drop=True)
        self._call_count = 0

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
        trial_dir = cfg.run_root / f"dream_{self._call_count:04d}"
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
            return np.full(len(self._observed), np.nan, dtype=float)
        aligned = align_series(self._observed, sim_df)
        if aligned.empty:
            return np.full(len(self._observed), np.nan, dtype=float)
        obs_ts = self._observed["timestamp"].to_numpy()
        sim_map = dict(
            zip(aligned["timestamp"].to_numpy(), aligned["flow_sim"].astype(float).to_numpy())
        )
        return np.array([sim_map.get(ts, np.nan) for ts in obs_ts], dtype=float)

    def evaluation(self) -> np.ndarray:
        return self._observed["flow"].astype(float).to_numpy()

    def objectivefunction(self, simulation, evaluation, params=None) -> float:
        """Return the log-likelihood ``-0.5 * (1 - KGE) / sigma^2``.

        DREAM with ``acceptance_test_option=6`` accepts a proposal when the
        returned ``like`` is greater than the chain's current best, so we
        encode "better fit -> larger likelihood" by returning the log-density
        directly. Failed simulations get a very negative likelihood so they
        cannot win acceptance.
        """

        sim = np.asarray(simulation, dtype=float)
        obs = np.asarray(evaluation, dtype=float)
        mask = np.isfinite(sim) & np.isfinite(obs)
        if mask.sum() < 2:
            return -1.0e9
        sim_ok = sim[mask]
        obs_ok = obs[mask]
        ts = self._observed["timestamp"].to_numpy()[mask]
        sim_df = pd.DataFrame({"timestamp": ts, "flow": sim_ok})
        obs_df = pd.DataFrame({"timestamp": ts, "flow": obs_ok})
        kge_result = kge(obs_df, sim_df)
        kge_value = kge_result["kge"]
        if kge_value is None or not math.isfinite(kge_value):
            return -1.0e9
        sigma = self.config.sigma
        return float(-0.5 * (1.0 - kge_value) / (sigma * sigma))

    @property
    def param_order(self) -> list[str]:
        return list(self._param_order)


# ---------------------------------------------------------------------------
# Posterior post-processing
# ---------------------------------------------------------------------------


def _extract_chain_field(results) -> np.ndarray | None:
    """Return the per-row chain index array from spotpy's result table, if present."""

    if results is None or len(results) == 0:
        return None
    if "chain" in results.dtype.names:
        return np.asarray(results["chain"]).astype(int)
    return None


def _split_burn_in(results, n_chains: int) -> tuple[np.ndarray, np.ndarray]:
    """Drop the regular-startpoint initialisation rows and return (post-burnin rows, chain ids).

    spotpy.algorithms.dream writes ``nChains`` initialisation rows (one per chain)
    before the random walk begins. We strip those rows from the posterior.
    """

    chains = _extract_chain_field(results)
    if chains is None:
        # Fall back to row-order interpretation: assume rows interleaved by chain.
        n = len(results)
        idx = np.arange(n)
        if n > n_chains:
            keep = idx >= n_chains
            return idx[keep], (idx[keep] % n_chains)
        return idx, idx % n_chains
    # Burn-in: per chain, drop the first sample.
    keep_mask = np.ones(len(results), dtype=bool)
    for ch in range(n_chains):
        ch_idx = np.where(chains == ch)[0]
        if ch_idx.size:
            keep_mask[ch_idx[0]] = False
    keep = np.where(keep_mask)[0]
    return keep, chains[keep]


def write_posterior_samples_csv(
    results,
    param_order: Sequence[str],
    n_chains: int,
    csv_path: Path,
) -> int:
    """Write the post-burn-in MCMC samples to CSV. Returns number of rows written."""

    keep_idx, chain_ids = _split_burn_in(results, n_chains)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    header = ["chain", "iteration_in_chain", "likelihood", *param_order]
    chain_seen: dict[int, int] = {}
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for row_pos, idx in enumerate(keep_idx):
            row = results[idx]
            ch = int(chain_ids[row_pos])
            chain_seen[ch] = chain_seen.get(ch, -1) + 1
            iter_in_chain = chain_seen[ch]
            like_val = float(row["like1"]) if "like1" in results.dtype.names else float("nan")
            param_values = []
            for name in param_order:
                col = f"par{name}"
                if col in results.dtype.names:
                    param_values.append(float(row[col]))
                else:
                    param_values.append(float("nan"))
            writer.writerow(
                [
                    ch,
                    iter_in_chain,
                    f"{like_val:.10g}",
                    *[f"{v:.10g}" for v in param_values],
                ]
            )
    return len(keep_idx)


def _gelman_rubin(chains: np.ndarray) -> float:
    """Compute the Gelman-Rubin Rhat for a single parameter.

    ``chains`` has shape (n_chains, n_samples).
    """

    m, n = chains.shape
    if m < 2 or n < 2:
        return float("nan")
    chain_means = chains.mean(axis=1)
    chain_vars = chains.var(axis=1, ddof=1)
    grand_mean = chain_means.mean()
    B = (n / (m - 1)) * np.sum((chain_means - grand_mean) ** 2)
    W = chain_vars.mean()
    if W <= 0.0 or not np.isfinite(W):
        return float("nan")
    var_hat = ((n - 1) / n) * W + (1.0 / n) * B
    rhat = math.sqrt(var_hat / W)
    if not math.isfinite(rhat):
        return float("nan")
    return float(rhat)


def compute_rhat(
    results,
    param_order: Sequence[str],
    n_chains: int,
) -> dict[str, float]:
    """Return Gelman-Rubin Rhat per parameter, using post-burn-in samples."""

    keep_idx, chain_ids = _split_burn_in(results, n_chains)
    rhat: dict[str, float] = {}
    for name in param_order:
        col = f"par{name}"
        if col not in results.dtype.names:
            rhat[name] = float("nan")
            continue
        per_chain: list[list[float]] = [[] for _ in range(n_chains)]
        for row_pos, idx in enumerate(keep_idx):
            ch = int(chain_ids[row_pos])
            if 0 <= ch < n_chains:
                per_chain[ch].append(float(results[idx][col]))
        min_len = min((len(c) for c in per_chain), default=0)
        if min_len < 2:
            rhat[name] = float("nan")
            continue
        arr = np.array([c[:min_len] for c in per_chain], dtype=float)
        rhat[name] = _gelman_rubin(arr)
    return rhat


def write_chain_convergence_json(
    rhat: dict[str, float],
    threshold: float,
    n_chains: int,
    iterations: int,
    json_path: Path,
) -> bool:
    """Write chain_convergence.json. Returns the converged flag.

    Per-parameter Rhat is emitted as numeric where finite and ``null`` where the
    Gelman-Rubin diagnostic could not be computed (e.g. zero between-chain
    variance after a very short run).
    """

    finite_vals = [v for v in rhat.values() if isinstance(v, (int, float)) and math.isfinite(v)]
    converged = bool(finite_vals) and all(v < threshold for v in finite_vals)
    payload = {
        "rhat": {
            name: (float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None)
            for name, v in rhat.items()
        },
        "threshold": float(threshold),
        "converged": converged,
        "n_chains": int(n_chains),
        "iterations": int(iterations),
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return converged


def _matplotlib_backend():
    """Return matplotlib.pyplot with a non-interactive backend forced."""

    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt  # noqa: WPS433

    return plt


def write_marginal_histograms(
    results,
    param_order: Sequence[str],
    n_chains: int,
    output_dir: Path,
) -> list[Path]:
    """Plot one marginal histogram per parameter using post-burn-in samples."""

    plt = _matplotlib_backend()
    keep_idx, _ = _split_burn_in(results, n_chains)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in param_order:
        col = f"par{name}"
        if col not in results.dtype.names:
            continue
        vals = np.array([float(results[idx][col]) for idx in keep_idx], dtype=float)
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            continue
        fig, ax = plt.subplots(figsize=(5.5, 3.5))
        bins = max(8, min(40, int(math.sqrt(vals.size))))
        ax.hist(vals, bins=bins, color="#4878D0", edgecolor="white", alpha=0.85)
        ax.set_xlabel(name)
        ax.set_ylabel("count")
        ax.set_title(f"Posterior marginal: {name}")
        fig.tight_layout()
        path = output_dir / f"posterior_{name}.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        written.append(path)
    return written


def write_correlation_plot(
    results,
    param_order: Sequence[str],
    n_chains: int,
    output_path: Path,
) -> None:
    """Plot the posterior parameter correlation matrix as a heatmap."""

    plt = _matplotlib_backend()
    keep_idx, _ = _split_burn_in(results, n_chains)
    if not param_order:
        return
    columns: list[np.ndarray] = []
    used_names: list[str] = []
    for name in param_order:
        col = f"par{name}"
        if col not in results.dtype.names:
            continue
        vals = np.array([float(results[idx][col]) for idx in keep_idx], dtype=float)
        columns.append(vals)
        used_names.append(name)
    if len(columns) < 1:
        return
    mat = np.vstack(columns)
    if mat.shape[1] < 2:
        # Degenerate: emit a one-cell heatmap so the artefact still exists.
        corr = np.array([[1.0]])
        used_names = used_names[:1] or ["param"]
    else:
        # Replace columns with NaN variance with their mean to avoid all-NaN rows.
        finite_mask = np.isfinite(mat).all(axis=0)
        mat = mat[:, finite_mask]
        if mat.shape[1] < 2:
            corr = np.array([[1.0]])
            used_names = used_names[:1] or ["param"]
        else:
            try:
                corr = np.corrcoef(mat)
            except Exception:
                corr = np.eye(len(used_names))
    fig, ax = plt.subplots(figsize=(1.2 * len(used_names) + 1.5, 1.0 * len(used_names) + 1.2))
    im = ax.imshow(corr, vmin=-1, vmax=1, cmap="RdBu_r", aspect="auto")
    ax.set_xticks(range(len(used_names)))
    ax.set_yticks(range(len(used_names)))
    ax.set_xticklabels(used_names, rotation=45, ha="right")
    ax.set_yticklabels(used_names)
    for i in range(len(used_names)):
        for j in range(len(used_names)):
            val = corr[i, j] if corr.shape == (len(used_names), len(used_names)) else 1.0
            ax.text(j, i, f"{val:.2f}", ha="center", va="center", color="black", fontsize=8)
    ax.set_title("Posterior parameter correlation")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=120)
    plt.close(fig)


def _pick_map_row(results) -> tuple[int, float] | None:
    """Pick the highest-likelihood (MAP) row index and its likelihood value."""

    if results is None or len(results) == 0 or "like1" not in results.dtype.names:
        return None
    likes = np.asarray(results["like1"], dtype=float)
    if not np.isfinite(likes).any():
        return None
    # Mask non-finite (failed sims) so they cannot be chosen.
    masked = np.where(np.isfinite(likes), likes, -np.inf)
    best_idx = int(np.argmax(masked))
    return best_idx, float(likes[best_idx])


def _summarise_posterior(results, param_order: Sequence[str], n_chains: int) -> dict[str, Any]:
    keep_idx, _ = _split_burn_in(results, n_chains)
    out: dict[str, Any] = {
        "n_chains": int(n_chains),
        "n_samples_post_burnin": int(len(keep_idx)),
        "per_parameter": {},
    }
    for name in param_order:
        col = f"par{name}"
        if col not in results.dtype.names:
            continue
        vals = np.array([float(results[idx][col]) for idx in keep_idx], dtype=float)
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            out["per_parameter"][name] = {
                "mean": None,
                "median": None,
                "std": None,
                "q05": None,
                "q95": None,
            }
            continue
        out["per_parameter"][name] = {
            "mean": float(vals.mean()),
            "median": float(np.median(vals)),
            "std": float(vals.std(ddof=1)) if vals.size > 1 else 0.0,
            "q05": float(np.quantile(vals, 0.05)),
            "q95": float(np.quantile(vals, 0.95)),
        }
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_dream_zs(config: DreamZsConfig) -> dict[str, Any]:
    """Run DREAM-ZS, write all 5 posterior artefacts + summary, return a results dict."""

    import spotpy  # noqa: WPS433
    from spotpy.algorithms import dream as dream_algo

    setup = _SwmmDreamSetup(config)
    sampler = dream_algo(
        setup,
        dbname=str(config.run_root / "dream_db"),
        dbformat="ram",
        random_state=config.seed,
        save_sim=False,
    )
    # spotpy.algorithms.dream has two structural constraints we must satisfy:
    #   1. ``nChains >= 2*delta + 1`` so each step has ``delta`` partner chains
    #      to form the differential proposal.
    #   2. ``get_r_hat`` only returns a numeric Rhat when ``nChains > 3``;
    #      with three or fewer chains it returns ``None`` and spotpy's
    #      convergence-limit check raises ``TypeError``.
    # When callers ask for fewer chains we lift the effective count to the
    # smallest value that lets the algorithm physically run; the requested
    # count is preserved in ``posterior_summary.n_chains_requested``.
    requested_chains = int(config.n_chains)
    delta = 3 if requested_chains >= 7 else 1
    min_chains_for_rhat = 4
    effective_chains = max(requested_chains, 2 * delta + 1, min_chains_for_rhat)
    sampler.sample(
        repetitions=config.iterations,
        nChains=effective_chains,
        delta=delta,
        convergence_limit=config.rhat_threshold,
        runs_after_convergence=config.runs_after_convergence,
    )

    results = sampler.getdata()
    if results is None or len(results) == 0:
        raise RuntimeError("DREAM-ZS results database was empty.")

    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    posterior_csv = output_dir / "posterior_samples.csv"
    convergence_json = output_dir / "chain_convergence.json"
    correlation_png = output_dir / "posterior_correlation.png"

    post_rows = write_posterior_samples_csv(
        results=results,
        param_order=setup.param_order,
        n_chains=effective_chains,
        csv_path=posterior_csv,
    )
    rhat = compute_rhat(results, setup.param_order, effective_chains)
    converged = write_chain_convergence_json(
        rhat=rhat,
        threshold=config.rhat_threshold,
        n_chains=effective_chains,
        iterations=config.iterations,
        json_path=convergence_json,
    )
    write_marginal_histograms(
        results=results,
        param_order=setup.param_order,
        n_chains=effective_chains,
        output_dir=output_dir,
    )
    write_correlation_plot(
        results=results,
        param_order=setup.param_order,
        n_chains=effective_chains,
        output_path=correlation_png,
    )

    # MAP estimate: highest-likelihood row in the database.
    best = _pick_map_row(results)
    if best is None:
        raise RuntimeError("DREAM-ZS could not identify a MAP-estimate row (no finite likelihoods).")
    best_idx, _ = best

    best_params: dict[str, float | int] = {}
    for name in setup.param_order:
        col = f"par{name}"
        if col in results.dtype.names:
            value = float(results[best_idx][col])
            bound = config.bounds[name]
            if bound.value_type == "int":
                best_params[name] = int(round(value))
            elif bound.precision is not None:
                best_params[name] = round(value, bound.precision)
            else:
                best_params[name] = value

    # Re-run SWMM at MAP to compute Slice 1 metrics & decomposition.
    cfg = config
    final_trial_dir = cfg.run_root / "dream_map"
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
        raise RuntimeError(f"swmm5 MAP run failed with rc={rc}")
    sim_df = cfg.extract_series(out_path)
    aligned = align_series(setup._observed, sim_df)
    metrics_bundle = compute_metrics(setup._observed, sim_df)
    kge_block = kge(setup._observed, sim_df)
    if kge_block["decomposition"] is None or kge_block["kge"] is None:
        raise RuntimeError("KGE undefined for the MAP parameter set; cannot summarise.")

    obs_for_pbias = (
        aligned["flow_obs"].astype(float)
        if not aligned.empty
        else setup._observed["flow"].astype(float)
    )
    secondary = secondary_metrics_from_bundle(metrics_bundle, obs_for_pbias)

    summary = build_calibration_summary(
        primary_value=kge_block["kge"],
        kge_decomposition=kge_block["decomposition"],
        secondary_metrics=secondary,
        iterations=config.iterations,
        convergence_trace_ref=convergence_json.name,
    )
    # Overwrite strategy name from SCE-UA default to DREAM-ZS.
    summary["strategy"] = STRATEGY_NAME

    # DREAM-specific posterior summary block (additional, non-breaking).
    posterior_summary = _summarise_posterior(results, setup.param_order, effective_chains)
    posterior_summary.update(
        {
            "n_chains": int(effective_chains),
            "n_chains_requested": int(requested_chains),
            "converged": bool(converged),
            "rhat_threshold": float(config.rhat_threshold),
            "rhat": {name: (float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None)
                     for name, v in rhat.items()},
            "sigma": float(config.sigma),
            "n_samples_total": int(len(results)),
            "posterior_csv_ref": posterior_csv.name,
            "correlation_png_ref": correlation_png.name,
        }
    )
    summary["posterior_summary"] = posterior_summary

    return {
        "summary": summary,
        "best_params": best_params,
        "posterior_samples_csv": str(posterior_csv),
        "chain_convergence_json": str(convergence_json),
        "correlation_png": str(correlation_png),
        "total_calls": setup._call_count,
        "metrics_bundle": metrics_bundle.to_dict(),
        "post_burnin_rows": int(post_rows),
        "rhat": rhat,
        "converged": bool(converged),
    }


if __name__ == "__main__":
    # CLI usage is via swmm_calibrate.py search --strategy dream-zs.
    import sys

    print(
        "dream_zs.py is a library module; invoke via 'swmm_calibrate.py search --strategy dream-zs'.",
        file=sys.stderr,
    )
    sys.exit(0)
