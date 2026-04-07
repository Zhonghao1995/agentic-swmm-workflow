---
name: swmm-calibration
description: Calibration, validation, and sensitivity-analysis scaffold for EPA SWMM. Use when Zhonghao asks to (1) compare simulated vs observed flow, (2) evaluate candidate parameter sets, (3) run a small sensitivity scan, or (4) expose calibration/validation as MCP tools for an agentic stormwater workflow.
---

# SWMM Calibration / Validation (MVP scaffold)

## What this skill provides
- A practical calibration scaffold around the existing SWMM runner workflow.
- Observed-flow ingestion from delimited text files (`.csv`, `.tsv`, `.dat`, whitespace-separated text).
- Metric calculation for simulated vs observed hydrographs:
  - NSE
  - RMSE
  - Bias
  - Peak flow error
  - Peak timing error
- Simple INP text patching using an explicit mapping from parameter names to line selectors.
- Batch evaluation of candidate parameter sets for:
  - `sensitivity`
  - `calibrate`
  - `validate`
- MCP wrapper so OpenClaw can call the workflow as tools.

## Scripts
- `scripts/swmm_calibrate.py`
  - `sensitivity` → evaluate many candidate parameter sets and rank them
  - `calibrate` → evaluate candidate parameter sets and report the best one
  - `validate` → apply one chosen parameter set to a second event and score it
- `scripts/obs_reader.py`
  - heuristically reads timestamp + flow series from text tables
- `scripts/metrics.py`
  - computes hydrograph comparison metrics after time alignment
- `scripts/inp_patch.py`
  - patches selected numeric tokens in an `.inp` file using a simple JSON mapping

## Expected workflow
1. Prepare a **base SWMM INP** for the event.
2. Prepare an **observed flow file** with at least one timestamp column and one flow column.
3. Define a **patch map JSON** that explains where each calibration parameter lives in the INP.
4. Prepare a **parameter sets JSON** (explicit candidate sets for MVP).
5. Run one of:
   - `sensitivity`
   - `calibrate`
   - `validate`
6. Inspect the output summary JSON and generated trial directories.

## MVP assumptions / limitations
- This is intentionally a **transparent scaffold**, not a black-box optimizer.
- Candidate parameter sets are currently supplied explicitly via JSON. The tool does **not** yet generate Latin Hypercube / DE samples internally.
- INP patching is line-oriented and works best for one-line table records with stable object names.
- Observed-flow parsing uses heuristics. If your file is messy, give explicit column names and time format whenever possible.
- Simulated flow is read either from:
  - SWMM `.out` (preferred, via `swmmtoolbox`), or
  - a delimited simulation series file.
- The validation command assumes you already chose a parameter set (via JSON object or file).

## Patch-map idea
A patch-map JSON connects friendly parameter names to concrete INP edits.

Example:
```json
{
  "pct_imperv_s1": {
    "section": "[SUBCATCHMENTS]",
    "object": "S1",
    "field_index": 4
  },
  "n_imperv_s1": {
    "section": "[SUBAREAS]",
    "object": "S1",
    "field_index": 1
  }
}
```

Interpretation:
- `section` = INP section header to search within
- `object` = first token on the target row
- `field_index` = zero-based token index within the data row

## Candidate parameter-set JSON idea
```json
[
  {"name": "trial_001", "params": {"pct_imperv_s1": 42.0, "n_imperv_s1": 0.015}},
  {"name": "trial_002", "params": {"pct_imperv_s1": 47.0, "n_imperv_s1": 0.018}}
]
```

## CLI examples
### Sensitivity scan
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py sensitivity \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map path/to/patch_map.json \
  --parameter-sets path/to/parameter_sets.json \
  --observed path/to/observed_flow.csv \
  --run-root runs/calibration \
  --swmm-node O1 \
  --objective nse
```

### Calibration (pick best candidate set)
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py calibrate \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map path/to/patch_map.json \
  --parameter-sets path/to/parameter_sets.json \
  --observed path/to/observed_flow.csv \
  --run-root runs/calibration \
  --swmm-node O1 \
  --objective nse
```

### Validation on a second event
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py validate \
  --base-inp path/to/validation_event.inp \
  --patch-map path/to/patch_map.json \
  --best-params path/to/best_params.json \
  --observed path/to/validation_observed.csv \
  --run-root runs/validation \
  --swmm-node O1
```

## Recommended near-term extensions
- Add internal search strategies (Latin Hypercube, random search, differential evolution)
- Add multi-event calibration/validation
- Add observed-vs-simulated overlay plots to the calibration script
- Extend patch-map selectors beyond simple one-line object rows
