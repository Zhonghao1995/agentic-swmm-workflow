---
name: swmm-calibration
description: Calibration and validation scaffold for EPA SWMM. Use when an agent needs to (1) compare simulated vs observed flow, (2) evaluate candidate parameter sets, (3) rank explicit candidates by an objective, (4) run a bounded random / LHS / adaptive search for the best-fitting parameters, or (5) run a publication-grade SCE-UA calibration with KGE as the primary objective and (r, alpha, beta) decomposition reported. Dedicated sensitivity-analysis methods (OAT, Morris, Sobol') now live on the `swmm-uncertainty` skill.
---

# SWMM Calibration / Validation (MVP scaffold)

## What this skill provides
- A practical calibration scaffold around the existing SWMM runner workflow.
- A strict calibration boundary: calibration and validation require observed data. Without observed flow, depth, soil-moisture, or volume data, use `swmm-uncertainty` for prior uncertainty propagation instead of calling the run calibrated.
- Observed-flow ingestion from delimited text files (`.csv`, `.tsv`, `.dat`, whitespace-separated text).
- Metric calculation for simulated vs observed hydrographs:
  - **KGE** (Kling-Gupta Efficiency) + (r, alpha, beta) decomposition — primary metric for publication-grade calibration.
  - NSE
  - RMSE
  - Bias / PBIAS%
  - Peak flow error
  - Peak timing error
- Simple INP text patching using an explicit mapping from parameter names to line selectors.
- Batch evaluation of candidate parameter sets for:
  - `sensitivity`
  - `calibrate`
  - `validate`
- Bounded internal search for calibration candidate generation:
  - `search --strategy random` — uniform random sampling (fast prototyping).
  - `search --strategy lhs` — Latin Hypercube Sampling (fast prototyping).
  - `search --strategy adaptive` — multi-round LHS refinement around elite trials (fast prototyping).
  - `search --strategy sceua` — Shuffled Complex Evolution (SCE-UA); recommended for publication-grade calibration. Minimises `(1 - KGE)` via `spotpy.algorithms.sceua` and emits a `calibration_summary.json` with KGE decomposition + secondary metrics. (DREAM-ZS posterior calibration is tracked as issue #53.)
- Dedicated sensitivity-analysis methods (OAT, Morris elementary-effects, Sobol' indices) have moved to the **swmm-uncertainty** skill — see `skills/swmm-uncertainty/scripts/sensitivity.py` and the `swmm_sensitivity_oat` / `swmm_sensitivity_morris` / `swmm_sensitivity_sobol` MCP tools.
- MCP wrapper so OpenClaw can call the workflow as tools.

### Strategy guidance

| Strategy   | When to use                                                | Cost       | Reports |
|------------|------------------------------------------------------------|------------|---------|
| `random`   | First-pass prototyping, smoke-testing the patch map        | Very low   | Ranking table |
| `lhs`      | Quick coverage of a small search space                     | Very low   | Ranking table |
| `adaptive` | LHS with multi-round refinement around elite trials        | Low        | Ranking table per round |
| **`sceua`**| **Publication-grade calibration on a fixed search space**  | **Medium** | **`calibration_summary.json` with KGE primary + decomposition + secondary metrics + convergence.csv** |
| (DREAM-ZS) | Bayesian posterior — tracked in #53                        | High       | Posterior chains, credible intervals |

## MCP tools

`mcp/swmm-calibration/server.js` exposes five tools, all thin wrappers around `scripts/swmm_calibrate.py`.

1. **`swmm_sensitivity_scan`** — evaluate a list of explicit candidate parameter sets against an observed series and rank them by an objective (KGE / NSE / RMSE / Bias / peak-flow / peak-timing). Use to score a curated candidate list. (This is *not* a screening method; for OAT / Morris / Sobol' screening use the `swmm_sensitivity_*` tools on the `swmm-uncertainty` MCP server.)

2. **`swmm_calibrate`** — same evaluation as above, but report the single best-scoring set and write a `best_params.json`. Use when you already have a curated candidate list.

3. **`swmm_calibrate_search`** — generate bounded candidate sets internally and score them. Strategies: `random`, `lhs`, `adaptive` (multi-round LHS refinement around elite trials). Use when you have a search-space JSON instead of an explicit candidate list.

4. **`swmm_calibrate_sceua`** — global SCE-UA calibration with KGE as the primary objective. Emits a `calibration_summary.json` containing `primary_objective`, `primary_value`, `kge_decomposition` (r / alpha / beta), `secondary_metrics` (NSE, PBIAS%, RMSE, peak-flow error, peak-timing error), and a `convergence.csv` trace. Use for publication-grade calibration. Requires the optional `spotpy` dependency.

5. **`swmm_validate`** — apply one chosen parameter set to a second event (validation) and score it.

> Sensitivity analysis (OAT / Morris / Sobol') is owned by `swmm-uncertainty`. See `mcp/swmm-uncertainty/server.js` for `swmm_sensitivity_oat`, `swmm_sensitivity_morris`, and `swmm_sensitivity_sobol`.

## Scripts (Python implementations behind the MCP tools)

- `scripts/swmm_calibrate.py` — backs `swmm_sensitivity_scan`, `swmm_calibrate`, `swmm_calibrate_search`, `swmm_validate`. Subcommands: `sensitivity`, `calibrate`, `search`, `validate`.
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

Uncertainty / sensitivity analysis asks:

```text
Given uncertain parameters, how much does the SWMM output ensemble spread,
and which parameters drive that spread?
```

Use this skill only when observed data are available and the workflow can compute metrics such as NSE, RMSE, bias, peak-flow error, or peak-timing error. If no observed data are available, use `swmm-uncertainty` for prior Monte Carlo, fuzzy, entropy, or sensitivity analysis.

Per issue #49 the OAT / Morris / Sobol' sensitivity-analysis path lives on `swmm-uncertainty` (`skills/swmm-uncertainty/scripts/sensitivity.py`). The calibration scaffold consumes its output via:

- `runs/<case>/09_audit/sensitivity_indices.json` — per-parameter ranking with `mu_star`/`sigma` (Morris) or `S_i`/`S_T_i` (Sobol'). Use this to pre-screen which parameters to feed into SCE-UA or LHS search.

A calibration run can feed uncertainty / sensitivity analysis back by exporting:

- `best_params.json` for a baseline parameter set
- `ranking.json` for candidate performance
- narrowed or acceptable parameter ranges for calibration-informed Monte Carlo

## MVP assumptions / limitations
- This is intentionally a **transparent scaffold**, not a black-box optimizer.
- Internal search supports bounded random, LHS-like sampling, simple adaptive LHS refinement, and SCE-UA (Shuffled Complex Evolution) for global optimisation against KGE. Bayesian posterior calibration (DREAM-ZS) is tracked in issue #53.
- INP patching is line-oriented and works best for one-line table records with stable object names.
- Observed-flow parsing uses heuristics. If your file is messy, give explicit column names and time format whenever possible.
- Simulated flow is read either from:
  - SWMM `.out` (preferred, via `swmmtoolbox`), or
  - a delimited simulation series file.
- The validation command assumes you already chose a parameter set (via JSON object or file).
- The `swmm_sensitivity_scan` tool here scores explicit candidate sets against an observed series; it is not parameter screening. Use `swmm-uncertainty`'s `swmm_sensitivity_oat` / `swmm_sensitivity_morris` / `swmm_sensitivity_sobol` tools for OAT / Morris / Sobol' screening.

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

### SCE-UA calibration (publication-grade, KGE primary)
Requires `spotpy` to be installed (it ships as a runtime dependency in `pyproject.toml`).

```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py search \
  --base-inp <your case>/event.inp \
  --patch-map <your case>/calibration/patch_map.json \
  --search-space <your case>/calibration/search_space.json \
  --observed <your case>/calibration/observed_flow.csv \
  --run-root runs/calibration-sceua \
  --summary-json runs/calibration-sceua/calibration_summary.json \
  --best-params-out runs/calibration-sceua/best_params.json \
  --convergence-csv runs/calibration-sceua/convergence.csv \
  --strategy sceua \
  --objective kge \
  --iterations 200 \
  --seed 42
```

`calibration_summary.json` shape:

```json
{
  "primary_objective": "kge",
  "primary_value": 0.78,
  "kge_decomposition": {"r": 0.92, "alpha": 1.05, "beta": 0.97},
  "secondary_metrics": {
    "nse": 0.74, "pbias_pct": -3.2, "rmse": 0.043,
    "peak_error_rel": 0.08, "peak_timing_min": 12
  },
  "strategy": "sceua",
  "iterations": 200,
  "convergence_trace_ref": "convergence.csv"
}
```

## Recommended near-term extensions
- Add DREAM-ZS Bayesian posterior calibration (issue #53) — complementary to SCE-UA's point estimate.
- Add multi-event calibration/validation.
- Add observed-vs-simulated overlay plots to the calibration script.
- Extend patch-map selectors beyond simple one-line object rows.
- Wire candidate handover artefacts (`candidate_calibration.json`, `candidate_inp_patch.json`, `calibration_report.md`) — issue #54.
