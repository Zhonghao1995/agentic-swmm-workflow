#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
import pandas as pd
from swmmtoolbox import swmmtoolbox

from inp_patch import patch_inp_text
from obs_reader import read_series


def filter_series_window(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    out = df.copy()
    out['timestamp'] = pd.to_datetime(out['timestamp'])
    if start:
        out = out[out['timestamp'] >= pd.Timestamp(start)]
    if end:
        out = out[out['timestamp'] <= pd.Timestamp(end)]
    return out.reset_index(drop=True)


def load_json(path: str | Path):
    return json.loads(Path(path).read_text())


def run_swmm(inp: Path, run_dir: Path) -> tuple[int, Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    rpt = run_dir / 'model.rpt'
    out = run_dir / 'model.out'
    proc = subprocess.run(['swmm5', str(inp), str(rpt), str(out)], capture_output=True, text=True)
    (run_dir / 'stdout.txt').write_text(proc.stdout, encoding='utf-8', errors='ignore')
    (run_dir / 'stderr.txt').write_text(proc.stderr, encoding='utf-8', errors='ignore')
    return proc.returncode, rpt, out


def extract_simulated_series(out_path: Path, swmm_node: str, swmm_attr: str, aggregate: str) -> pd.DataFrame:
    label = f'node,{swmm_node},{swmm_attr}'
    series = swmmtoolbox.extract(str(out_path), label)
    if isinstance(series, pd.Series):
        df = series.reset_index()
        df.columns = ['timestamp', 'flow']
    else:
        df = series.reset_index()
        df.columns = ['timestamp', 'flow']
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    if aggregate == 'daily_mean':
        df = df.set_index('timestamp').resample('D').mean(numeric_only=True).reset_index()
    return df


def evaluate(base_inp: Path, patch_map: dict, params: dict, observed: pd.DataFrame, run_dir: Path,
             swmm_node: str, swmm_attr: str, aggregate: str) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    patched = patch_inp_text(base_inp.read_text(errors='ignore'), patch_map, params)
    inp = run_dir / 'model.inp'
    inp.write_text(patched, encoding='utf-8')
    rc, rpt, out = run_swmm(inp, run_dir)
    rec = {'params': params, 'run_dir': str(run_dir), 'return_code': rc}
    if rc != 0:
        return rec
    sim = extract_simulated_series(out, swmm_node=swmm_node, swmm_attr=swmm_attr, aggregate=aggregate)
    merged = pd.merge(observed, sim, on='timestamp', how='inner', suffixes=('_obs', '_sim'))
    obs = merged['flow_obs']
    simv = merged['flow_sim']
    diff = simv - obs
    den = ((obs - obs.mean()) ** 2).sum()
    nse = None if den == 0 else float(1 - ((diff.pow(2).sum()) / den))
    rmse = float(math.sqrt((diff.pow(2).mean())))
    peak_err_abs = float(abs(simv.max() - obs.max()))
    mean_err_abs = float(abs(simv.mean() - obs.mean()))
    rec.update({
        'nse': nse,
        'rmse': rmse,
        'peak_err_abs': peak_err_abs,
        'mean_err_abs': mean_err_abs,
        'max_sim': float(simv.max()),
        'max_obs': float(obs.max()),
        'mean_sim': float(simv.mean()),
        'mean_obs': float(obs.mean()),
    })
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base-inp', required=True, type=Path)
    ap.add_argument('--patch-map', required=True, type=Path)
    ap.add_argument('--base-params', required=True, type=Path, help='JSON object for baseline parameter values')
    ap.add_argument('--scan-spec', required=True, type=Path, help='JSON object: parameter -> list of trial values')
    ap.add_argument('--observed', required=True, type=Path)
    ap.add_argument('--run-root', required=True, type=Path)
    ap.add_argument('--summary-json', required=True, type=Path)
    ap.add_argument('--swmm-node', default='O1')
    ap.add_argument('--swmm-attr', default='Total_inflow')
    ap.add_argument('--aggregate', choices=['none', 'daily_mean'], default='none')
    ap.add_argument('--timestamp-col', default=None)
    ap.add_argument('--flow-col', default=None)
    ap.add_argument('--time-format', default=None)
    ap.add_argument('--obs-start', default=None, help='Inclusive observed-series window start, e.g. 1984-05-23')
    ap.add_argument('--obs-end', default=None, help='Inclusive observed-series window end, e.g. 1984-05-28')
    args = ap.parse_args()

    patch_map = load_json(args.patch_map)
    base_params = load_json(args.base_params)
    scan_spec = load_json(args.scan_spec)
    observed = read_series(args.observed, timestamp_col=args.timestamp_col, flow_col=args.flow_col, time_format=args.time_format)
    observed = filter_series_window(observed, args.obs_start, args.obs_end)

    out = {'parameters': []}
    for pname, values in scan_spec.items():
        trials = []
        for idx, val in enumerate(values):
            params = dict(base_params)
            params[pname] = val
            run_dir = args.run_root / f'{pname}_{idx}'
            trials.append(evaluate(args.base_inp, patch_map, params, observed, run_dir, args.swmm_node, args.swmm_attr, args.aggregate))
        valid = [t for t in trials if t.get('return_code') == 0 and t.get('rmse') is not None]
        if valid:
            rmses = [t['rmse'] for t in valid]
            peaks = [t['peak_err_abs'] for t in valid]
            importance = (max(rmses) - min(rmses)) + (max(peaks) - min(peaks))
            best = min(valid, key=lambda x: x['rmse'] + x['peak_err_abs'] + x['mean_err_abs'])
            base_val = base_params[pname]
            if best['params'][pname] < base_val:
                direction = 'down'
                next_range = [min(values), base_val]
            elif best['params'][pname] > base_val:
                direction = 'up'
                next_range = [base_val, max(values)]
            else:
                direction = 'stay'
                next_range = [min(values), max(values)]
        else:
            importance = None
            best = None
            direction = 'unclear'
            next_range = [min(values), max(values)]
        out['parameters'].append({
            'parameter': pname,
            'tested_values': values,
            'importance': importance,
            'recommended_direction': direction,
            'suggested_next_range': next_range,
            'best_trial': best,
            'trials': valid,
        })

    out['parameters'].sort(key=lambda x: x['importance'] if x['importance'] is not None else -1, reverse=True)
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
