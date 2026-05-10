#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PYTHON = sys.executable


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def write_json(path: str | Path, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def run_cmd(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def build_common_args(ns: argparse.Namespace) -> list[str]:
    args = [
        "--base-inp", str(ns.base_inp),
        "--patch-map", str(ns.patch_map),
        "--observed", str(ns.observed),
        "--swmm-node", ns.swmm_node,
        "--swmm-attr", ns.swmm_attr,
        "--aggregate", ns.aggregate,
    ]
    if ns.timestamp_col:
        args += ["--timestamp-col", ns.timestamp_col]
    if ns.flow_col:
        args += ["--flow-col", ns.flow_col]
    if ns.time_format:
        args += ["--time-format", ns.time_format]
    if ns.obs_start:
        args += ["--obs-start", ns.obs_start]
    if ns.obs_end:
        args += ["--obs-end", ns.obs_end]
    return args


def pick_top_parameters(scout_summary: dict, top_k: int) -> list[dict]:
    params = scout_summary.get("parameters", [])
    params = [p for p in params if p.get("importance") is not None]
    params.sort(key=lambda x: x.get("importance", -1), reverse=True)
    return params[:top_k]


def candidate_values_for_param(param_rec: dict, base_params: dict) -> list[Any]:
    pname = param_rec["parameter"]
    values = list(param_rec.get("tested_values", []))
    best = param_rec.get("best_trial", {})
    best_params = best.get("params", {})
    base_val = base_params[pname]
    best_val = best_params.get(pname, base_val)
    direction = param_rec.get("recommended_direction", "stay")

    ordered = []
    for v in [best_val, base_val]:
        if v not in ordered:
            ordered.append(v)

    if direction == "down":
        down_vals = [v for v in values if v < base_val]
        if down_vals:
            v = min(down_vals, key=lambda x: abs(x - best_val))
            if v not in ordered:
                ordered.append(v)
    elif direction == "up":
        up_vals = [v for v in values if v > base_val]
        if up_vals:
            v = min(up_vals, key=lambda x: abs(x - best_val))
            if v not in ordered:
                ordered.append(v)

    return ordered[:3]


def generate_parameter_sets(base_params: dict, selected: list[dict], max_candidates: int) -> list[dict]:
    per_param = []
    for rec in selected:
        pname = rec["parameter"]
        vals = candidate_values_for_param(rec, base_params)
        per_param.append((pname, vals))

    combos = []
    seen = set()
    for combo in itertools.product(*[vals for _, vals in per_param]):
        params = dict(base_params)
        name_bits = []
        for (pname, _), value in zip(per_param, combo):
            params[pname] = value
            name_bits.append(f"{pname}_{str(value).replace('.', 'p')}")
        key = tuple((p, params[p]) for p, _ in per_param)
        if key in seen:
            continue
        seen.add(key)
        combos.append({"name": "__".join(name_bits), "params": params})
        if len(combos) >= max_candidates:
            break
    return combos


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-inp", required=True, type=Path)
    ap.add_argument("--patch-map", required=True, type=Path)
    ap.add_argument("--base-params", required=True, type=Path)
    ap.add_argument("--scan-spec", required=True, type=Path)
    ap.add_argument("--observed", required=True, type=Path)
    ap.add_argument("--run-root", required=True, type=Path)
    ap.add_argument("--swmm-node", default="O1")
    ap.add_argument("--swmm-attr", default="Total_inflow")
    ap.add_argument("--aggregate", choices=["none", "daily_mean"], default="none")
    ap.add_argument("--objective", default="nse", choices=["nse", "rmse", "bias", "peak_flow_error", "peak_timing_error"])
    ap.add_argument("--timestamp-col", default=None)
    ap.add_argument("--flow-col", default=None)
    ap.add_argument("--time-format", default=None)
    ap.add_argument("--obs-start", default=None)
    ap.add_argument("--obs-end", default=None)
    ap.add_argument("--top-k-params", type=int, default=2)
    ap.add_argument("--max-candidates", type=int, default=9)
    ap.add_argument("--final-summary", default=None, type=Path)
    args = ap.parse_args()

    run_root = args.run_root
    run_root.mkdir(parents=True, exist_ok=True)
    common = build_common_args(args)

    round_scout = run_root / "round_01_scout"
    scout_summary = round_scout / "summary.json"
    run_cmd([
        PYTHON, "skills/swmm-calibration/scripts/parameter_scout.py",
        "--base-inp", str(args.base_inp),
        "--patch-map", str(args.patch_map),
        "--base-params", str(args.base_params),
        "--scan-spec", str(args.scan_spec),
        "--run-root", str(round_scout),
        "--summary-json", str(scout_summary),
        *common,
    ])

    scout_obj = load_json(scout_summary)
    base_params = load_json(args.base_params)
    selected = pick_top_parameters(scout_obj, args.top_k_params)
    generated_sets = generate_parameter_sets(base_params, selected, args.max_candidates)
    generated_path = run_root / "generated_parameter_sets.json"
    write_json(generated_path, generated_sets)

    round_cal = run_root / "round_01_calibration"
    cal_summary = round_cal / "summary.json"
    best_params_out = run_root / "best_params.json"
    run_cmd([
        PYTHON, "skills/swmm-calibration/scripts/swmm_calibrate.py", "calibrate",
        "--base-inp", str(args.base_inp),
        "--patch-map", str(args.patch_map),
        "--parameter-sets", str(generated_path),
        "--run-root", str(round_cal),
        "--summary-json", str(cal_summary),
        "--best-params-out", str(best_params_out),
        "--objective", args.objective,
        *common,
    ])

    cal_obj = load_json(cal_summary)
    final_summary = {
        "mode": "iterative_calibration_mvp",
        "objective": args.objective,
        "selected_parameters": [
            {
                "parameter": rec["parameter"],
                "importance": rec.get("importance"),
                "recommended_direction": rec.get("recommended_direction"),
                "suggested_next_range": rec.get("suggested_next_range"),
                "best_trial": rec.get("best_trial"),
            }
            for rec in selected
        ],
        "generated_parameter_sets": str(generated_path),
        "scout_summary": str(scout_summary),
        "calibration_summary": str(cal_summary),
        "best_params": load_json(best_params_out) if best_params_out.exists() else None,
        "best_result": cal_obj.get("best"),
    }
    manifest = {
        "workflow": "iterative_calibration_mvp",
        "inputs": {
            "base_inp": str(args.base_inp),
            "patch_map": str(args.patch_map),
            "base_params": str(args.base_params),
            "scan_spec": str(args.scan_spec),
            "observed": str(args.observed),
            "swmm_node": args.swmm_node,
            "swmm_attr": args.swmm_attr,
            "aggregate": args.aggregate,
            "objective": args.objective,
            "obs_start": args.obs_start,
            "obs_end": args.obs_end,
            "top_k_params": args.top_k_params,
            "max_candidates": args.max_candidates,
        },
        "artifacts": {
            "round_01_scout": str(round_scout),
            "round_01_scout_summary": str(scout_summary),
            "generated_parameter_sets": str(generated_path),
            "round_01_calibration": str(round_cal),
            "round_01_calibration_summary": str(cal_summary),
            "best_params": str(best_params_out),
        },
        "selected_parameters": final_summary["selected_parameters"],
        "best_result": final_summary["best_result"],
    }
    write_json(run_root / "manifest.json", manifest)
    final_path = args.final_summary or (run_root / "final_summary.json")
    write_json(final_path, final_summary)
    print(json.dumps(final_summary, indent=2))


if __name__ == "__main__":
    main()
