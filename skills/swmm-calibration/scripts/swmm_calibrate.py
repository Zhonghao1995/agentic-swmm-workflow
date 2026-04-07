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
from metrics import compute_metrics, score_from_metrics
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


def extract_simulated_series(out_path: Path, swmm_node: str, swmm_attr: str) -> pd.DataFrame:
    label = f"node,{swmm_node},{swmm_attr}"
    series = swmmtoolbox.extract(str(out_path), label)
    if isinstance(series, pd.Series):
        df = series.reset_index()
        df.columns = ["timestamp", "flow"]
        return df
    if isinstance(series, pd.DataFrame):
        df = series.reset_index()
        df.columns = ["timestamp", "flow"]
        return df[["timestamp", "flow"]]
    raise TypeError(f"Unexpected series type from swmmtoolbox: {type(series)}")


def run_swmm(inp: Path, run_dir: Path, rpt_name: str = "model.rpt", out_name: str = "model.out") -> tuple[int, Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    rpt = run_dir / rpt_name
    out = run_dir / out_name
    proc = subprocess.run(["swmm5", str(inp), str(rpt), str(out)], capture_output=True, text=True)
    (run_dir / "stdout.txt").write_text(proc.stdout, encoding="utf-8", errors="ignore")
    (run_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8", errors="ignore")
    return proc.returncode, rpt, out


def evaluate_trial(base_inp: Path, patch_map: dict, trial: dict, observed_path: Path, run_root: Path,
                   swmm_node: str, swmm_attr: str, objective: str,
                   timestamp_col: str | None, flow_col: str | None, time_format: str | None,
                   dry_run: bool = False) -> dict:
    trial_name = trial["name"]
    trial_dir = run_root / trial_name
    trial_dir.mkdir(parents=True, exist_ok=True)
    patched_inp = trial_dir / "model.inp"
    patched_text = patch_inp_text(base_inp.read_text(errors="ignore"), patch_map, trial["params"])
    patched_inp.write_text(patched_text, encoding="utf-8")

    observed = read_series(observed_path, timestamp_col=timestamp_col, flow_col=flow_col, time_format=time_format)

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

    simulated = extract_simulated_series(out, swmm_node=swmm_node, swmm_attr=swmm_attr)
    metrics = compute_metrics(observed, simulated)
    result["metrics"] = metrics.to_dict()
    result["objective"] = score_from_metrics(metrics, objective)
    return result


def rank_results(results: list[dict]) -> list[dict]:
    return sorted(results, key=lambda x: (x["objective"] is None, -(x["objective"] or float("-inf"))))


def cmd_sensitivity(args: argparse.Namespace) -> None:
    patch_map = load_json(args.patch_map)
    trials = [ensure_named_trial(t, i + 1) for i, t in enumerate(ensure_param_sets(load_json(args.parameter_sets)))]
    results = [evaluate_trial(
        args.base_inp, patch_map, trial, args.observed, args.run_root,
        args.swmm_node, args.swmm_attr, args.objective,
        args.timestamp_col, args.flow_col, args.time_format,
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
        args.swmm_node, args.swmm_attr, args.objective,
        args.timestamp_col, args.flow_col, args.time_format,
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
        args.swmm_node, args.swmm_attr, args.objective,
        args.timestamp_col, args.flow_col, args.time_format,
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
        sp.add_argument("--timestamp-col", default=None)
        sp.add_argument("--flow-col", default=None)
        sp.add_argument("--time-format", default=None)
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
