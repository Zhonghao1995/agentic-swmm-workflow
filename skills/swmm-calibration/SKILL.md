---
name: swmm-calibration
description: Calibration, validation, and sensitivity-analysis scaffold for EPA SWMM. Use when an agent needs to (1) compare simulated vs observed flow, (2) evaluate candidate parameter sets, (3) run a small sensitivity scan, (4) run a bounded random / LHS / adaptive search for the best-fitting parameters, or (5) suggest which parameters matter most under the current metric.
---

# SWMM Calibration / Validation (MVP scaffold)

## What this skill provides
- A practical calibration scaffold around the existing SWMM runner workflow.
- A strict calibration boundary: calibration and validation require observed data. Without observed flow, depth, soil-moisture, or volume data, use `swmm-uncertainty` for prior uncertainty propagation instead of calling the run calibrated.
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
- Bounded internal search for calibration candidate generation:
  - `search --strategy random`
  - `search --strategy lhs`
  - `search --strategy adaptive` (multi-round LHS refinement around elite trials)
- A minimal **parameter scout** that ranks which parameters matter most, suggests direction (`up` / `down` / `stay`), and proposes a narrowed next search range.
- MCP wrapper so OpenClaw can call the workflow as tools.

## MCP tools

`mcp/swmm-calibration/server.js` exposes five tools, all of them thin wrappers around `scripts/swmm_calibrate.py` + `scripts/parameter_scout.py`.

1. **`swmm_sensitivity_scan`** — evaluate a list of candidate parameter sets against an observed series and rank them by an objective (NSE / RMSE / Bias / peak-flow / peak-timing). Use to map parameter influence before committing to a search.

2. **`swmm_calibrate`** — same evaluation as above, but report the single best-scoring set and write a `best_params.json`. Use when you already have a curated candidate list.

3. **`swmm_calibrate_search`** — generate bounded candidate sets internally and score them. Strategies: `random`, `lhs`, `adaptive` (multi-round LHS refinement around elite trials). Use when you have a search-space JSON instead of an explicit candidate list.

4. **`swmm_validate`** — apply one chosen parameter set to a second event (validation) and score it.

5. **`swmm_parameter_scout`** — scan one parameter at a time around a baseline; rank which parameters matter most under the current metric and time scale; recommend direction (`up` / `down` / `stay`) and a narrower next search range. Use as a cheap warm-up before running `swmm_calibrate_search`.

## Scripts (Python implementations behind the MCP tools)

- `scripts/swmm_calibrate.py` — backs `swmm_sensitivity_scan`, `swmm_calibrate`, `swmm_calibrate_search`, `swmm_validate`. Subcommands: `sensitivity`, `calibrate`, `search`, `validate`.
- `scripts/parameter_scout.py` — backs `swmm_parameter_scout`.
- `scripts/obs_reader.py` — heuristically reads timestamp + flow series from text tables.
- `scripts/metrics.py` — computes hydrograph comparison metrics after time alignment.
- `scripts/inp_patch.py` — patches selected numeric tokens in an `.inp` file using a simple JSON mapping.

## Expected workflow
1. Prepare a **base SWMM INP** for the event.
2. Prepare an **observed flow file** with at least one timestamp column and one flow column.
3. Define a **patch map JSON** that explains where each calibration parameter lives in the INP.
4. Prepare either:
   - a **parameter sets JSON** (explicit candidate sets), or
   - a **search-space JSON** (`min/max/type/precision`) for internal bounded search.
5. Run one of:
   - `sensitivity`
   - `calibrate`
   - `validate`
6. Inspect the output summary JSON and generated trial directories.

## Relationship to uncertainty analysis

`swmm-calibration` and `swmm-uncertainty` share parameter patching but answer different questions.

Calibration asks:

```text
Given observed data, which parameter set best reproduces the observed hydrograph?
```

Uncertainty analysis asks:

```text
Given uncertain parameters, how much does the SWMM output ensemble spread?
```

Use this skill only when observed data are available and the workflow can compute metrics such as NSE, RMSE, bias, peak-flow error, or peak-timing error. If no observed data are available, use `swmm-uncertainty` for prior Monte Carlo, fuzzy, or entropy analysis.

A calibration run can feed uncertainty analysis by exporting:

- `best_params.json` for a baseline parameter set
- `ranking.json` for candidate performance
- narrowed or acceptable parameter ranges for calibration-informed Monte Carlo

## MVP assumptions / limitations
- This is intentionally a **transparent scaffold**, not a black-box optimizer.
- Internal search currently supports bounded random, LHS-like sampling, and simple adaptive LHS refinement. It does not yet include advanced optimizers (e.g., DE/CMA-ES/Bayesian).
- INP patching is line-oriented and works best for one-line table records with stable object names.
- Observed-flow parsing uses heuristics. If your file is messy, give explicit column names and time format whenever possible.
- Simulated flow is read either from:
  - SWMM `.out` (preferred, via `swmmtoolbox`), or
  - a delimited simulation series file.
- The validation command assumes you already chose a parameter set (via JSON object or file).
- Sensitivity scans without observed data are not calibration; they are parameter screening or uncertainty setup.

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

## Search-space JSON idea
```json
{
  "pct_imperv_s1": {"min": 15.0, "max": 40.0, "type": "float", "precision": 3},
  "n_imperv_s1": {"min": 0.01, "max": 0.03, "type": "float", "precision": 4}
}
```

## CLI examples
### Sensitivity scan
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py sensitivity \
  --base-inp <your case>/event.inp \
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
  --base-inp <your case>/event.inp \
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

### Internal bounded search (LHS)
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py search \
  --base-inp <your case>/event.inp \
  --patch-map <your case>/calibration/patch_map.json \
  --search-space <your case>/calibration/search_space.json \
  --observed <your case>/calibration/observed_flow.csv \
  --run-root runs/calibration-search \
  --summary-json runs/calibration-search/summary.json \
  --ranking-json runs/calibration-search/ranking.json \
  --strategy lhs \
  --iterations 12 \
  --seed 42
```

### Internal bounded search (adaptive multi-round)
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py search \
  --base-inp <your case>/event.inp \
  --patch-map <your case>/calibration/patch_map.json \
  --search-space <your case>/calibration/search_space.json \
  --observed <your case>/calibration/observed_flow.csv \
  --run-root runs/calibration-search-adaptive \
  --summary-json runs/calibration-search-adaptive/summary.json \
  --strategy adaptive \
  --iterations 8 \
  --rounds 3 \
  --seed 42
```

## Recommended near-term extensions
- Add stronger optimizers after bounded search (differential evolution / CMA-ES / Bayesian)
- Add multi-event calibration/validation
- Add observed-vs-simulated overlay plots to the calibration script
- Extend patch-map selectors beyond simple one-line object rows
