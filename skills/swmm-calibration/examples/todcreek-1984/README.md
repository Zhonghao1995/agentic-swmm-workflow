# Tod Creek 1984 real-case calibration config

This folder is the local real-case config layer for `skills/swmm-calibration`.

## Real source files
- Observed flow: `/Users/zhonghao/.openclaw/workspace/projects/swmm-mcp/data/Todcreek/Flow/1984Rflow.dat`
- Base SWMM input: `/Users/zhonghao/.openclaw/workspace/projects/swmm-mcp/todcreek/runs/run_1984-05-25/model.inp`

## Current case assumptions
- Target node: `O1`
- Target subcatchment/object: `S1`
- Observed file is a SWMM-style time series text file with comment lines beginning with `;;`
- The observed series is daily, so comparisons work best with `--aggregate daily_mean` (scout + calibrate + validate)
- The current base INP is an event-window model, not a full-season model: it runs from `1984-05-23` to `1984-05-27` with `END_TIME 24:00:00`, so event-scale daily comparison should explicitly use `--obs-start 1984-05-23 --obs-end 1984-05-28`

## Files in this folder
- `patch_map.json`: maps friendly parameter names to concrete `S1` edits in the INP
- `base_params.json`: current baseline parameter values for `S1`
- `scan_spec.json`: one-parameter-at-a-time scout values
- `parameter_sets.json`: a small explicit trial set for calibration MVP

## Example commands

### Parameter scout on the daily observed series
```bash
python3 skills/swmm-calibration/scripts/parameter_scout.py \
  --base-inp projects/swmm-mcp/todcreek/runs/run_1984-05-25/model.inp \
  --patch-map skills/swmm-calibration/examples/todcreek-1984/patch_map.json \
  --base-params skills/swmm-calibration/examples/todcreek-1984/base_params.json \
  --scan-spec skills/swmm-calibration/examples/todcreek-1984/scan_spec.json \
  --observed projects/swmm-mcp/data/Todcreek/Flow/1984Rflow.dat \
  --run-root runs/swmm-calibration/todcreek-1984-scout \
  --summary-json runs/swmm-calibration/todcreek-1984-scout/summary.json \
  --swmm-node O1 \
  --aggregate daily_mean
```

### MVP calibration pass with explicit trial sets
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py calibrate \
  --base-inp projects/swmm-mcp/todcreek/runs/run_1984-05-25/model.inp \
  --patch-map skills/swmm-calibration/examples/todcreek-1984/patch_map.json \
  --parameter-sets skills/swmm-calibration/examples/todcreek-1984/parameter_sets.json \
  --observed projects/swmm-mcp/data/Todcreek/Flow/1984Rflow.dat \
  --run-root runs/swmm-calibration/todcreek-1984-calibration \
  --summary-json runs/swmm-calibration/todcreek-1984-calibration/summary.json \
  --best-params-out runs/swmm-calibration/todcreek-1984-calibration/best_params.json \
  --swmm-node O1 \
  --aggregate daily_mean \
  --obs-start 1984-05-23 \
  --obs-end 1984-05-28
```

## Notes
- `swmm_calibrate.py` supports `--aggregate daily_mean` for this case to compare daily observed data against simulated daily means.
- `swmm_calibrate.py` and `parameter_scout.py` also support `--obs-start/--obs-end` so event-window calibration can be explicit instead of relying on accidental overlap with a longer observed record.
- If `summary.json` shows a very small `metrics.count`, that is usually a window mismatch, not a math bug. Check the base INP start/end dates against the observed series range.
- This folder is local-development config, not the cleaned publish-repo example layer.
