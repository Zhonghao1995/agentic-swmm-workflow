# Calibration example (MVP)

This folder contains a **minimal example configuration** for the public calibration scaffold.

## Files
- `patch_map.json` → maps friendly parameter names to concrete SWMM INP row/field edits
- `parameter_sets.json` → explicit candidate parameter sets for sensitivity / calibration demo
- `search_space.json` → bounded search-space spec for internal randomized / LHS / adaptive search
- `observed_flow.csv` → tiny mock observed-flow file for wiring and dry-run tests

## Real observed-flow source
The supported public path for this example is the synthetic
`observed_flow.csv` shipped in this folder. It is intentionally tiny and
exists only so the calibration scaffold wiring can be exercised end to
end without external dependencies.

To turn this into a real calibration case, **bring your own observed-flow
file** (any timestamped flow CSV with a column for the SWMM outflow node
of interest) and substitute it via `--observed <your-file>`. The
maintainer's private workspace contains a Tod Creek `1984Rflow.dat`
record; that file is not redistributable through this repository, so
each user must supply their own observed series.

## Recommended use
### 1) Dry-run the wiring
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py calibrate \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --parameter-sets examples/calibration/parameter_sets.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/calibration-demo \
  --swmm-node O1 \
  --objective nse \
  --summary-json runs/calibration-demo/summary.json \
  --best-params-out runs/calibration-demo/best_params.json \
  --dry-run
```

### 2) Swap in real observed flow
Once the observed-flow parser is tuned for your data file, replace `--observed` with the real file path and run without `--dry-run`.

### 3) Run bounded search (LHS)
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py search \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --search-space examples/calibration/search_space.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/calibration-search-demo \
  --summary-json runs/calibration-search-demo/summary.json \
  --ranking-json runs/calibration-search-demo/ranking.json \
  --strategy lhs \
  --iterations 10 \
  --seed 42 \
  --dry-run
```

### 4) Run bounded search (adaptive refinement)
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py search \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --search-space examples/calibration/search_space.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/calibration-search-adaptive-demo \
  --summary-json runs/calibration-search-adaptive-demo/summary.json \
  --strategy adaptive \
  --iterations 6 \
  --rounds 3 \
  --seed 42 \
  --dry-run
```

## Parameter scout example
A minimal scout pass can rank one-parameter-at-a-time influence around a baseline parameter set.

Expected extra inputs:
- `base_params.json` → baseline parameter object
- `scan_spec.json` → parameter name to tested values

Example command:
```bash
python3 skills/swmm-calibration/scripts/parameter_scout.py \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --base-params examples/calibration/base_params.json \
  --scan-spec examples/calibration/scan_spec.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/parameter-scout \
  --summary-json runs/parameter-scout/summary.json \
  --swmm-node O1
```

## Current limitations
- The mock CSV here is only for demonstration.
- Internal search is bounded (`random` / `lhs` / simple adaptive LHS) and does not yet use advanced global optimizers.
- The scout is one-parameter-at-a-time; it does not capture full parameter interaction.
- The current parser expects a timestamp column and a flow column; special SWMM timeseries formats may need light pre-cleaning or parser extension.

## New summary diagnostics
`summary.json` now includes richer diagnostics for each trial:
- `status` (`ok`, `invalid`, `failed`, `dry_run`)
- `reason_code` + `reason_detail` for failed/invalid runs
- `diagnostics` with timing, overlap fraction, and observed/simulated counts
- `ranking_table` for quick machine-readable ranking
