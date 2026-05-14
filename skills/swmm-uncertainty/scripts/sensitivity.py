#!/usr/bin/env python3
"""Unified sensitivity-analysis entry point for swmm-uncertainty.

Slice 4 (#49) ports `swmm-calibration/scripts/parameter_scout.py` to
`swmm-uncertainty/scripts/sensitivity.py` and extends it with two
variance-based methods backed by SALib:

    --method oat      One-at-a-time perturbation around a baseline; ranks
                      parameters by an RMSE+peak-error importance score.
                      Output mirrors the legacy parameter_scout summary so
                      downstream consumers (calibration scaffold, audit
                      run folder) keep working.
    --method morris   Morris elementary-effects (mu_star, sigma) at sample
                      budget r * (k + 1).
    --method sobol    Sobol' indices (first-order S_i, total-effect S_T_i)
                      at sample budget N * (2k + 2).

All three modes share an `inp_patch` workflow: a base INP, a patch-map
JSON describing where each named parameter sits in the INP, and an
observed series used to score each trial. The OAT branch consumes a
`scan_spec.json` (the old parameter_scout shape); the Morris and Sobol'
branches consume a `parameter_space.json` keyed by name with `min`/`max`.

Outputs are written to `--summary-json` (the issue spec puts this at
`runs/<case>/09_audit/sensitivity_indices.json`).

The scoring objective for Morris/Sobol' is RMSE between simulated and
observed flow at the target node. RMSE is monotone in deviation, so
larger output sensitivity directly maps to larger index values without
needing a sign convention.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

# Reuse calibration helpers (inp_patch, obs_reader) so the move does not
# duplicate code. CONTEXT.md treats those as cross-skill primitives.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
CALIBRATION_SCRIPTS = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"
if str(CALIBRATION_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CALIBRATION_SCRIPTS))

from inp_patch import patch_inp_text  # noqa: E402
from obs_reader import read_series  # noqa: E402

from swmmtoolbox import swmmtoolbox  # noqa: E402


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def filter_series_window(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    if start:
        out = out[out["timestamp"] >= pd.Timestamp(start)]
    if end:
        out = out[out["timestamp"] <= pd.Timestamp(end)]
    return out.reset_index(drop=True)


def run_swmm(inp: Path, run_dir: Path) -> tuple[int, Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    rpt = run_dir / "model.rpt"
    out = run_dir / "model.out"
    proc = subprocess.run(
        ["swmm5", str(inp), str(rpt), str(out)],
        capture_output=True,
        text=True,
    )
    (run_dir / "stdout.txt").write_text(proc.stdout, encoding="utf-8", errors="ignore")
    (run_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8", errors="ignore")
    return proc.returncode, rpt, out


def extract_simulated_series(
    out_path: Path,
    swmm_node: str,
    swmm_attr: str,
    aggregate: str,
) -> pd.DataFrame:
    label = f"node,{swmm_node},{swmm_attr}"
    series = swmmtoolbox.extract(str(out_path), label)
    df = series.reset_index()
    df.columns = ["timestamp", "flow"]
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if aggregate == "daily_mean":
        df = df.set_index("timestamp").resample("D").mean(numeric_only=True).reset_index()
    return df


def evaluate_trial(
    base_inp: Path,
    patch_map: dict,
    params: dict,
    observed: pd.DataFrame,
    run_dir: Path,
    swmm_node: str,
    swmm_attr: str,
    aggregate: str,
) -> dict:
    """Patch the INP, run swmm5, compute RMSE/peak/mean errors vs observed.

    Same scoring shape parameter_scout used. Returning a partial record on
    swmm5 failure (return_code != 0) is intentional: the caller can mark
    the trial invalid without aborting the whole sensitivity scan.
    """

    run_dir.mkdir(parents=True, exist_ok=True)
    patched = patch_inp_text(base_inp.read_text(errors="ignore"), patch_map, params)
    inp = run_dir / "model.inp"
    inp.write_text(patched, encoding="utf-8")
    rc, _, out_path = run_swmm(inp, run_dir)
    rec: dict[str, Any] = {"params": params, "run_dir": str(run_dir), "return_code": rc}
    if rc != 0:
        return rec
    sim = extract_simulated_series(out_path, swmm_node, swmm_attr, aggregate)
    merged = pd.merge(observed, sim, on="timestamp", how="inner", suffixes=("_obs", "_sim"))
    if merged.empty:
        return rec
    obs = merged["flow_obs"].astype(float)
    simv = merged["flow_sim"].astype(float)
    diff = simv - obs
    den = float(((obs - obs.mean()) ** 2).sum())
    nse = None if den == 0 else float(1 - ((diff.pow(2).sum()) / den))
    rmse = float(math.sqrt((diff.pow(2).mean())))
    peak_err_abs = float(abs(simv.max() - obs.max()))
    mean_err_abs = float(abs(simv.mean() - obs.mean()))
    rec.update({
        "nse": nse,
        "rmse": rmse,
        "peak_err_abs": peak_err_abs,
        "mean_err_abs": mean_err_abs,
        "max_sim": float(simv.max()),
        "max_obs": float(obs.max()),
        "mean_sim": float(simv.mean()),
        "mean_obs": float(obs.mean()),
    })
    return rec


def write_summary(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# OAT (port of parameter_scout)
# ---------------------------------------------------------------------------


def run_oat(args: argparse.Namespace, observed: pd.DataFrame) -> dict:
    patch_map = load_json(args.patch_map)
    base_params = load_json(args.base_params)
    scan_spec = load_json(args.scan_spec)
    summary: dict[str, Any] = {"method": "oat", "parameters": []}
    for pname, values in scan_spec.items():
        trials: list[dict] = []
        for idx, val in enumerate(values):
            params = dict(base_params)
            params[pname] = val
            run_dir = args.run_root / f"{pname}_{idx}"
            trials.append(
                evaluate_trial(
                    args.base_inp,
                    patch_map,
                    params,
                    observed,
                    run_dir,
                    args.swmm_node,
                    args.swmm_attr,
                    args.aggregate,
                )
            )
        valid = [t for t in trials if t.get("return_code") == 0 and t.get("rmse") is not None]
        if valid:
            rmses = [t["rmse"] for t in valid]
            peaks = [t["peak_err_abs"] for t in valid]
            importance = (max(rmses) - min(rmses)) + (max(peaks) - min(peaks))
            best = min(valid, key=lambda x: x["rmse"] + x["peak_err_abs"] + x["mean_err_abs"])
            base_val = base_params[pname]
            if best["params"][pname] < base_val:
                direction = "down"
                next_range = [min(values), base_val]
            elif best["params"][pname] > base_val:
                direction = "up"
                next_range = [base_val, max(values)]
            else:
                direction = "stay"
                next_range = [min(values), max(values)]
        else:
            importance = None
            best = None
            direction = "unclear"
            next_range = [min(values), max(values)]
        summary["parameters"].append({
            "parameter": pname,
            "tested_values": values,
            "importance": importance,
            "recommended_direction": direction,
            "suggested_next_range": next_range,
            "best_trial": best,
            "trials": valid,
        })

    summary["parameters"].sort(
        key=lambda x: x["importance"] if x["importance"] is not None else -1,
        reverse=True,
    )
    return summary


# ---------------------------------------------------------------------------
# Variance-based helpers (Morris, Sobol')
# ---------------------------------------------------------------------------


def _build_salib_problem(parameter_space: dict) -> tuple[dict, list[str]]:
    """Turn `parameter_space.json` into a SALib `problem` dict.

    Names are read in insertion order so the sample matrix columns line up
    with the parameter names we report back.
    """

    if not parameter_space:
        raise ValueError("parameter_space.json must contain at least one parameter")
    names: list[str] = []
    bounds: list[list[float]] = []
    for name, spec in parameter_space.items():
        if "min" not in spec or "max" not in spec:
            raise ValueError(f"Parameter {name!r} must define both 'min' and 'max'")
        names.append(name)
        bounds.append([float(spec["min"]), float(spec["max"])])
    return {"num_vars": len(names), "names": names, "bounds": bounds}, names


def _score_rmse(rec: dict) -> float:
    """Convert an evaluate_trial record to an RMSE for SA scoring.

    Failed runs become +inf so SALib has a numeric to work with; this is
    consistent with treating a crashed swmm5 as "infinite deviation".
    """

    if rec.get("return_code") != 0 or rec.get("rmse") is None:
        return float("inf")
    return float(rec["rmse"])


def _propagate_samples(
    args: argparse.Namespace,
    samples,
    names: list[str],
    observed: pd.DataFrame,
) -> tuple[list[dict], list[float]]:
    """Run SWMM for each sample row; return per-row records and RMSE vector.

    `samples` is a 2-D NumPy array of shape (N_trials, k); row `i`
    becomes `dict(zip(names, samples[i]))` after patching.
    """

    patch_map = load_json(args.patch_map)
    records: list[dict] = []
    scores: list[float] = []
    for i, row in enumerate(samples):
        params = {name: float(value) for name, value in zip(names, row)}
        run_dir = args.run_root / f"trial_{i:04d}"
        rec = evaluate_trial(
            args.base_inp,
            patch_map,
            params,
            observed,
            run_dir,
            args.swmm_node,
            args.swmm_attr,
            args.aggregate,
        )
        records.append(rec)
        scores.append(_score_rmse(rec))
    return records, scores


def run_morris(args: argparse.Namespace, observed: pd.DataFrame) -> dict:
    """Morris elementary-effects via SALib.

    Sample budget = r * (k + 1), with `r = args.morris_r` trajectories and
    `k = len(parameter_space)` parameters. Outputs `mu_star` + `sigma`
    per parameter.
    """

    import numpy as np
    from SALib.sample import morris as morris_sampler
    from SALib.analyze import morris as morris_analyzer

    parameter_space = load_json(args.parameter_space)
    problem, names = _build_salib_problem(parameter_space)
    k = problem["num_vars"]
    r = int(args.morris_r)
    num_levels = int(args.morris_levels)

    samples = morris_sampler.sample(
        problem,
        N=r,
        num_levels=num_levels,
        seed=args.seed,
    )
    expected_budget = r * (k + 1)
    actual_budget = int(samples.shape[0])
    # SALib aligns the trajectory budget to r*(k+1) exactly; assert it so
    # the acceptance criterion never silently drifts.
    if actual_budget != expected_budget:
        raise RuntimeError(
            f"Morris sample budget mismatch: r*(k+1)={expected_budget} but SALib returned {actual_budget}"
        )

    records, scores = _propagate_samples(args, samples, names, observed)
    y = np.asarray(scores, dtype=float)

    # SALib's analyze chokes on non-finite scores. Replace inf with the
    # finite max so the index calculation can still complete; the trial
    # records preserve the original failure signal.
    finite_mask = np.isfinite(y)
    if not finite_mask.any():
        raise RuntimeError("All Morris trials failed; cannot compute indices")
    if not finite_mask.all():
        finite_max = float(y[finite_mask].max())
        y = np.where(finite_mask, y, finite_max)

    si = morris_analyzer.analyze(problem, samples, y, num_levels=num_levels, print_to_console=False)
    indices: dict[str, dict[str, float]] = {}
    for i, name in enumerate(names):
        indices[name] = {
            "mu": float(si["mu"][i]),
            "mu_star": float(si["mu_star"][i]),
            "sigma": float(si["sigma"][i]),
            "mu_star_conf": float(si["mu_star_conf"][i]),
        }

    return {
        "method": "morris",
        "objective": "rmse",
        "parameters": names,
        "sample_budget": expected_budget,
        "morris": {"r": r, "num_levels": num_levels},
        "indices": indices,
        "trials": records,
    }


def run_sobol(args: argparse.Namespace, observed: pd.DataFrame) -> dict:
    """Sobol' indices via SALib (saltelli + sobol.analyze).

    Sample budget = N * (2k + 2), with `N = args.sobol_n`. Outputs first-
    order `S_i` + total-effect `S_T_i`. We use the new `SALib.sample.sobol`
    helper (Saltelli sampling), which lines up with the budget formula and
    avoids the deprecation warning attached to the legacy import path.
    """

    import numpy as np
    from SALib.sample import sobol as sobol_sampler
    from SALib.analyze import sobol as sobol_analyzer

    parameter_space = load_json(args.parameter_space)
    problem, names = _build_salib_problem(parameter_space)
    k = problem["num_vars"]
    N = int(args.sobol_n)

    # The Saltelli budget formula in the issue spec — N * (2k + 2) — is the
    # variant with second-order interactions enabled. We don't *expose* the
    # S_ij matrix in sensitivity_indices.json (only first-order S_i and
    # total-effect S_T_i), but using calc_second_order=True keeps the
    # budget mathematically aligned with the acceptance criterion.
    samples = sobol_sampler.sample(problem, N, calc_second_order=True, seed=args.seed)
    expected_budget = N * (2 * k + 2)
    actual_budget = int(samples.shape[0])
    if actual_budget != expected_budget:
        raise RuntimeError(
            f"Sobol sample budget mismatch: N*(2k+2)={expected_budget} but SALib returned {actual_budget}"
        )

    records, scores = _propagate_samples(args, samples, names, observed)
    y = np.asarray(scores, dtype=float)
    finite_mask = np.isfinite(y)
    if not finite_mask.any():
        raise RuntimeError("All Sobol' trials failed; cannot compute indices")
    if not finite_mask.all():
        finite_max = float(y[finite_mask].max())
        y = np.where(finite_mask, y, finite_max)

    si = sobol_analyzer.analyze(problem, y, calc_second_order=True, print_to_console=False)
    indices: dict[str, dict[str, float]] = {}
    for i, name in enumerate(names):
        indices[name] = {
            "S_i": float(si["S1"][i]),
            "S_i_conf": float(si["S1_conf"][i]),
            "S_T_i": float(si["ST"][i]),
            "S_T_i_conf": float(si["ST_conf"][i]),
        }

    return {
        "method": "sobol",
        "objective": "rmse",
        "parameters": names,
        "sample_budget": expected_budget,
        "sobol": {"N": N, "calc_second_order": True},
        "indices": indices,
        "trials": records,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--method",
        required=True,
        choices=["oat", "morris", "sobol"],
        help="Which sensitivity-analysis sub-method to run.",
    )
    ap.add_argument("--base-inp", required=True, type=Path)
    ap.add_argument("--patch-map", required=True, type=Path)
    ap.add_argument("--observed", required=True, type=Path)
    ap.add_argument("--run-root", required=True, type=Path)
    ap.add_argument("--summary-json", required=True, type=Path)
    ap.add_argument("--swmm-node", default="O1")
    ap.add_argument("--swmm-attr", default="Total_inflow")
    ap.add_argument("--aggregate", choices=["none", "daily_mean"], default="none")
    ap.add_argument("--timestamp-col", default=None)
    ap.add_argument("--flow-col", default=None)
    ap.add_argument("--time-format", default=None)
    ap.add_argument("--obs-start", default=None)
    ap.add_argument("--obs-end", default=None)
    ap.add_argument("--seed", type=int, default=42)

    # OAT-specific.
    ap.add_argument(
        "--base-params",
        type=Path,
        help="JSON object with the baseline parameter values (OAT only).",
    )
    ap.add_argument(
        "--scan-spec",
        type=Path,
        help="JSON object: parameter -> list of trial values (OAT only).",
    )

    # Variance-based (Morris/Sobol').
    ap.add_argument(
        "--parameter-space",
        type=Path,
        help="JSON object: parameter -> {min, max, type?} (Morris and Sobol').",
    )
    ap.add_argument(
        "--morris-r",
        type=int,
        default=10,
        help="Number of Morris trajectories (sample budget = r*(k+1)).",
    )
    ap.add_argument(
        "--morris-levels",
        type=int,
        default=4,
        help="Number of grid levels for Morris (default 4, the SALib default).",
    )
    ap.add_argument(
        "--sobol-n",
        type=int,
        default=256,
        help="Saltelli base sample size (sample budget = N*(2k+2)).",
    )
    return ap


def main() -> None:
    args = build_argparser().parse_args()

    observed = read_series(
        args.observed,
        timestamp_col=args.timestamp_col,
        flow_col=args.flow_col,
        time_format=args.time_format,
    )
    observed = filter_series_window(observed, args.obs_start, args.obs_end)

    args.run_root.mkdir(parents=True, exist_ok=True)

    if args.method == "oat":
        if args.base_params is None or args.scan_spec is None:
            raise SystemExit("--method oat requires --base-params and --scan-spec")
        summary = run_oat(args, observed)
    elif args.method == "morris":
        if args.parameter_space is None:
            raise SystemExit("--method morris requires --parameter-space")
        summary = run_morris(args, observed)
    elif args.method == "sobol":
        if args.parameter_space is None:
            raise SystemExit("--method sobol requires --parameter-space")
        summary = run_sobol(args, observed)
    else:  # pragma: no cover - argparse choices guard
        raise SystemExit(f"Unsupported --method: {args.method}")

    write_summary(args.summary_json, summary)
    # Stay quiet on stdout for non-OAT methods so the per-trial swmm5
    # output never floods CI logs; the JSON file is the contract.
    print(json.dumps({"method": summary["method"], "summary_json": str(args.summary_json)}, indent=2))


if __name__ == "__main__":
    main()
