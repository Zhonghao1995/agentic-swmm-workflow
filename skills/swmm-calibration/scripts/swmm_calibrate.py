#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
from swmmtoolbox import swmmtoolbox

from inp_patch import patch_inp_text
from metrics import align_series, compute_metrics, score_from_metrics
from obs_reader import read_series


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def ensure_param_sets(obj: Any) -> list[dict]:
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and "parameter_sets" in obj:
        return obj["parameter_sets"]
    raise ValueError("Parameter sets JSON must be a list or contain 'parameter_sets'")


def ensure_named_trial(item: dict, idx: int) -> dict:
    out = dict(item)
    out.setdefault("name", f"trial_{idx:03d}")
    if "params" not in out or not isinstance(out["params"], dict):
        raise ValueError(f"Trial {out['name']} missing params object")
    return out


def extract_simulated_series(out_path: Path, swmm_node: str, swmm_attr: str, aggregate: str) -> pd.DataFrame:
    label = f"node,{swmm_node},{swmm_attr}"
    series = swmmtoolbox.extract(str(out_path), label)
    if isinstance(series, pd.Series):
        df = series.reset_index()
        df.columns = ["timestamp", "flow"]
    if isinstance(series, pd.DataFrame):
        df = series.reset_index()
        df.columns = ["timestamp", "flow"]
        df = df[["timestamp", "flow"]]
    if not isinstance(series, (pd.Series, pd.DataFrame)):
        raise TypeError(f"Unexpected series type from swmmtoolbox: {type(series)}")

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if aggregate == "daily_mean":
        df = df.set_index("timestamp").resample("D").mean(numeric_only=True).reset_index()
    return df


def run_swmm(inp: Path, run_dir: Path, rpt_name: str = "model.rpt", out_name: str = "model.out") -> tuple[int, Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    rpt = run_dir / rpt_name
    out = run_dir / out_name
    proc = subprocess.run(["swmm5", str(inp), str(rpt), str(out)], capture_output=True, text=True)
    (run_dir / "stdout.txt").write_text(proc.stdout, encoding="utf-8", errors="ignore")
    (run_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8", errors="ignore")
    return proc.returncode, rpt, out


def describe_series(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"count": 0, "start": None, "end": None}
    ts = pd.to_datetime(df["timestamp"])
    return {
        "count": int(len(df)),
        "start": ts.min().isoformat(),
        "end": ts.max().isoformat(),
    }


def filter_series_window(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    if start:
        out = out[out["timestamp"] >= pd.Timestamp(start)]
    if end:
        out = out[out["timestamp"] <= pd.Timestamp(end)]
    return out.reset_index(drop=True)


def evaluate_trial(base_inp: Path, patch_map: dict, trial: dict, observed_path: Path, run_root: Path,
                   swmm_node: str, swmm_attr: str, objective: str, aggregate: str,
                   timestamp_col: str | None, flow_col: str | None, time_format: str | None,
                   obs_start: str | None, obs_end: str | None,
                   dry_run: bool = False) -> dict:
    trial_name = trial["name"]
    trial_dir = run_root / trial_name
    trial_dir.mkdir(parents=True, exist_ok=True)
    patched_inp = trial_dir / "model.inp"
    patched_text = patch_inp_text(base_inp.read_text(errors="ignore"), patch_map, trial["params"])
    patched_inp.write_text(patched_text, encoding="utf-8")

    observed = read_series(observed_path, timestamp_col=timestamp_col, flow_col=flow_col, time_format=time_format)
    observed = filter_series_window(observed, obs_start, obs_end)

    result = {
        "trial": trial_name,
        "params": trial["params"],
        "run_dir": str(trial_dir),
        "dry_run": dry_run,
    }
    if dry_run:
        result["metrics"] = None
        result["objective"] = None
        return result

    rc, rpt, out = run_swmm(patched_inp, trial_dir)
    result["return_code"] = rc
    result["files"] = {"inp": str(patched_inp), "rpt": str(rpt), "out": str(out)}
    if rc != 0:
        result["metrics"] = None
        result["objective"] = None
        result["error"] = "swmm5 execution failed"
        return result

    simulated = extract_simulated_series(out, swmm_node=swmm_node, swmm_attr=swmm_attr, aggregate=aggregate)
    aligned = align_series(observed, simulated)
    metrics = compute_metrics(observed, simulated)
    result["observed_series"] = describe_series(observed)
    result["simulated_series"] = describe_series(simulated)
    result["aligned_series"] = describe_series(aligned.rename(columns={"flow_obs": "flow"}))
    result["metrics"] = metrics.to_dict()
    result["objective"] = score_from_metrics(metrics, objective)
    if len(observed) > 0 and metrics.count < len(observed):
        result["warning"] = (
            "Low overlap between observed and simulated timestamps. "
            "Check whether the base INP simulation window matches the observed record window."
        )
    return result


def rank_results(results: list[dict]) -> list[dict]:
    return sorted(results, key=lambda x: (x["objective"] is None, -(x["objective"] or float("-inf"))))


def cmd_sensitivity(args: argparse.Namespace) -> None:
    patch_map = load_json(args.patch_map)
    trials = [ensure_named_trial(t, i + 1) for i, t in enumerate(ensure_param_sets(load_json(args.parameter_sets)))]
    results = [evaluate_trial(
        args.base_inp, patch_map, trial, args.observed, args.run_root,
        args.swmm_node, args.swmm_attr, args.objective, args.aggregate,
        args.timestamp_col, args.flow_col, args.time_format,
        args.obs_start, args.obs_end,
        dry_run=args.dry_run,
    ) for trial in trials]
    ranked = rank_results(results)
    payload = {"mode": "sensitivity", "objective": args.objective, "results": ranked}
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


def cmd_calibrate(args: argparse.Namespace) -> None:
    patch_map = load_json(args.patch_map)
    trials = [ensure_named_trial(t, i + 1) for i, t in enumerate(ensure_param_sets(load_json(args.parameter_sets)))]
    results = [evaluate_trial(
        args.base_inp, patch_map, trial, args.observed, args.run_root,
        args.swmm_node, args.swmm_attr, args.objective, args.aggregate,
        args.timestamp_col, args.flow_col, args.time_format,
        args.obs_start, args.obs_end,
        dry_run=args.dry_run,
    ) for trial in trials]
    ranked = rank_results(results)
    best = ranked[0] if ranked else None
    payload = {"mode": "calibrate", "objective": args.objective, "best": best, "results": ranked}
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.best_params_out and best:
        Path(args.best_params_out).write_text(json.dumps(best["params"], indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


def cmd_validate(args: argparse.Namespace) -> None:
    patch_map = load_json(args.patch_map)
    params_obj = load_json(args.best_params)
    trial = {"name": args.trial_name, "params": params_obj}
    result = evaluate_trial(
        args.base_inp, patch_map, trial, args.observed, args.run_root,
        args.swmm_node, args.swmm_attr, args.objective, args.aggregate,
        args.timestamp_col, args.flow_col, args.time_format,
        args.obs_start, args.obs_end,
        dry_run=args.dry_run,
    )
    payload = {"mode": "validate", "objective": args.objective, "result": result}
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(sp: argparse.ArgumentParser, include_param_sets: bool = True) -> None:
        sp.add_argument("--base-inp", required=True, type=Path)
        sp.add_argument("--patch-map", required=True, type=Path)
        if include_param_sets:
            sp.add_argument("--parameter-sets", required=True, type=Path)
        sp.add_argument("--observed", required=True, type=Path)
        sp.add_argument("--run-root", required=True, type=Path)
        sp.add_argument("--swmm-node", default="O1")
        sp.add_argument("--swmm-attr", default="Total_inflow")
        sp.add_argument("--objective", default="nse", choices=["nse", "rmse", "bias", "peak_flow_error", "peak_timing_error"])
        sp.add_argument("--aggregate", choices=["none", "daily_mean"], default="none")
        sp.add_argument("--timestamp-col", default=None)
        sp.add_argument("--flow-col", default=None)
        sp.add_argument("--time-format", default=None)
        sp.add_argument("--obs-start", default=None, help="Inclusive observed-series window start, e.g. 1984-05-23")
        sp.add_argument("--obs-end", default=None, help="Inclusive observed-series window end, e.g. 1984-05-28")
        sp.add_argument("--summary-json", required=True, type=Path)
        sp.add_argument("--dry-run", action="store_true")

    sp_s = sub.add_parser("sensitivity")
    add_common(sp_s, include_param_sets=True)
    sp_s.set_defaults(func=cmd_sensitivity)

    sp_c = sub.add_parser("calibrate")
    add_common(sp_c, include_param_sets=True)
    sp_c.add_argument("--best-params-out", default=None, type=Path)
    sp_c.set_defaults(func=cmd_calibrate)

    sp_v = sub.add_parser("validate")
    add_common(sp_v, include_param_sets=False)
    sp_v.add_argument("--best-params", required=True, type=Path)
    sp_v.add_argument("--trial-name", default="validation")
    sp_v.set_defaults(func=cmd_validate)
    return ap


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
