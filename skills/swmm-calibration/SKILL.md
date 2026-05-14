---
name: swmm-calibration
description: Calibration and validation scaffold for EPA SWMM. Use when an agent needs to (1) compare simulated vs observed flow, (2) evaluate candidate parameter sets, (3) rank explicit candidates by an objective, (4) run a bounded random / LHS / adaptive search for the best-fitting parameters, (5) run a publication-grade SCE-UA calibration with KGE as the primary objective and (r, alpha, beta) decomposition reported, or (6) run a DREAM-ZS Bayesian calibration producing a posterior over parameters with Gelman-Rubin convergence checks. Dedicated sensitivity-analysis methods (OAT, Morris, Sobol') now live on the `swmm-uncertainty` skill.
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
  - `search --strategy sceua` — Shuffled Complex Evolution (SCE-UA); recommended for publication-grade point-estimate calibration. Minimises `(1 - KGE)` via `spotpy.algorithms.sceua` and emits a `calibration_summary.json` with KGE decomposition + secondary metrics.
  - `search --strategy dream-zs` — DREAM-ZS Bayesian calibration with a KGE-based likelihood `exp(-0.5 * (1 - KGE) / sigma^2)`. Produces a posterior over parameters via `spotpy.algorithms.dream`, writes 5 audit artefacts (`posterior_samples.csv`, `best_params.json`, `chain_convergence.json`, `posterior_<param>.png`, `posterior_correlation.png`) plus a Slice 1 -compatible `calibration_summary.json` with a `posterior_summary` block (Gelman-Rubin Rhat per parameter + per-parameter quantiles).
- Dedicated sensitivity-analysis methods (OAT, Morris elementary-effects, Sobol' indices) have moved to the **swmm-uncertainty** skill — see `skills/swmm-uncertainty/scripts/sensitivity.py` and the `swmm_sensitivity_oat` / `swmm_sensitivity_morris` / `swmm_sensitivity_sobol` MCP tools.
- MCP wrapper so OpenClaw can call the workflow as tools.

### Strategy guidance

| Strategy     | When to use                                                                | Cost       | Reports |
|--------------|----------------------------------------------------------------------------|------------|---------|
| `random`     | First-pass prototyping, smoke-testing the patch map                        | Very low   | Ranking table |
| `lhs`        | Quick coverage of a small search space                                     | Very low   | Ranking table |
| `adaptive`   | LHS with multi-round refinement around elite trials                        | Low        | Ranking table per round |
| **`sceua`**  | **Publication-grade point-estimate calibration on a fixed search space**   | **Medium** | **`calibration_summary.json` with KGE primary + decomposition + secondary metrics + `convergence.csv`** |
| **`dream-zs`** | **Bayesian posterior calibration with Gelman-Rubin convergence checks**  | **High**   | **`calibration_summary.json` + `posterior_samples.csv` + `chain_convergence.json` + per-parameter marginal PNGs + correlation PNG** |

## MCP tools

`mcp/swmm-calibration/server.js` exposes six tools, all thin wrappers around `scripts/swmm_calibrate.py`.

1. **`swmm_sensitivity_scan`** — evaluate a list of explicit candidate parameter sets against an observed series and rank them by an objective (KGE / NSE / RMSE / Bias / peak-flow / peak-timing). Use to score a curated candidate list. (This is *not* a screening method; for OAT / Morris / Sobol' screening use the `swmm_sensitivity_*` tools on the `swmm-uncertainty` MCP server.)

2. **`swmm_calibrate`** — same evaluation as above, but report the single best-scoring set and write a `best_params.json`. Use when you already have a curated candidate list.

3. **`swmm_calibrate_search`** — generate bounded candidate sets internally and score them. Strategies: `random`, `lhs`, `adaptive` (multi-round LHS refinement around elite trials). Use when you have a search-space JSON instead of an explicit candidate list.

4. **`swmm_calibrate_sceua`** — global SCE-UA calibration with KGE as the primary objective. Emits a `calibration_summary.json` containing `primary_objective`, `primary_value`, `kge_decomposition` (r / alpha / beta), `secondary_metrics` (NSE, PBIAS%, RMSE, peak-flow error, peak-timing error), and a `convergence.csv` trace. Use for publication-grade point-estimate calibration. Requires the optional `spotpy` dependency.

5. **`swmm_calibrate_dream_zs`** — DREAM-ZS Bayesian posterior calibration with a KGE-based likelihood `exp(-0.5 * (1 - KGE) / sigma^2)`. Writes 5 posterior artefacts to the chosen audit directory (defaults to the parent of `summaryJson`): `posterior_samples.csv` (post-burn-in MCMC samples), `best_params.json` (MAP estimate), `chain_convergence.json` (Gelman-Rubin Rhat per parameter), `posterior_<param>.png` (marginal histogram per parameter), `posterior_correlation.png` (parameter correlation matrix). The `calibration_summary.json` keeps the Slice 1 shape (primary_objective=`kge`, primary_value, kge_decomposition, secondary_metrics) plus a `posterior_summary` block with chain count, Rhat values, and per-parameter quantiles. Use for Bayesian uncertainty quantification on top of (or instead of) the SCE-UA point estimate. Requires the optional `spotpy` dependency.

6. **`swmm_validate`** — apply one chosen parameter set to a second event (validation) and score it.

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
- Internal search supports bounded random, LHS-like sampling, simple adaptive LHS refinement, SCE-UA (Shuffled Complex Evolution) for global optimisation against KGE, and DREAM-ZS (DiffeRential Evolution Adaptive Metropolis) for KGE-likelihood posterior sampling. SCE-UA produces a point estimate; DREAM-ZS produces a posterior plus a MAP point estimate.
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

### DREAM-ZS Bayesian calibration (posterior over parameters)
Requires `spotpy` (already a runtime dependency). Likelihood is `exp(-0.5 * (1 - KGE) / sigma^2)`.

```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py search \
  --base-inp <your case>/event.inp \
  --patch-map <your case>/calibration/patch_map.json \
  --search-space <your case>/calibration/search_space.json \
  --observed <your case>/calibration/observed_flow.csv \
  --run-root runs/calibration-dream-zs/trials \
  --summary-json runs/calibration-dream-zs/09_audit/calibration_summary.json \
  --dream-output-dir runs/calibration-dream-zs/09_audit \
  --best-params-out runs/calibration-dream-zs/09_audit/best_params.json \
  --strategy dream-zs \
  --objective kge \
  --iterations 2000 \
  --dream-chains 4 \
  --dream-sigma 0.1 \
  --dream-rhat-threshold 1.2 \
  --seed 42
```

The `09_audit/` folder will contain five DREAM-ZS artefacts plus `calibration_summary.json`:

- `posterior_samples.csv` — post-burn-in MCMC samples (`chain`, `iteration_in_chain`, `likelihood`, one column per parameter).
- `best_params.json` — MAP estimate (highest-likelihood row from the chains).
- `chain_convergence.json` — Gelman-Rubin Rhat per parameter, threshold, and a `converged` flag.
- `posterior_<param>.png` — marginal histogram per parameter.
- `posterior_correlation.png` — posterior parameter correlation matrix.

`calibration_summary.json` keeps the same shape as SCE-UA (so downstream tooling stays compatible) and adds a `posterior_summary` block:

```json
{
  "primary_objective": "kge",
  "primary_value": 0.83,
  "kge_decomposition": {"r": 0.94, "alpha": 1.02, "beta": 0.99},
  "secondary_metrics": {"nse": 0.79, "pbias_pct": -1.4, "rmse": 0.038, "peak_error_rel": 0.05, "peak_timing_min": 8},
  "strategy": "dream-zs",
  "iterations": 2000,
  "convergence_trace_ref": "chain_convergence.json",
  "posterior_summary": {
    "n_chains": 4,
    "n_chains_requested": 4,
    "n_samples_post_burnin": 1996,
    "converged": true,
    "rhat_threshold": 1.2,
    "rhat": {"pct_imperv_s1": 1.07, "n_imperv_s1": 1.04, "...": "..."},
    "per_parameter": {
      "pct_imperv_s1": {"mean": 29.7, "median": 29.8, "std": 1.2, "q05": 27.6, "q95": 31.5}
    }
  }
}
```

## Candidate handover contract (issue #54)

Calibration runs **never** patch the canonical INP. Every strategy
(random / lhs / adaptive / SCE-UA / DREAM-ZS) emits three artefacts to
`<run_dir>/09_audit/` when invoked with `--candidate-run-dir <run_dir>`:

| Artefact | Purpose |
|---|---|
| `candidate_calibration.json` | Best params + KGE + decomposition + secondary metrics + `evidence_boundary: "candidate_not_accepted_yet"` + SHA256 of the patch file + (DREAM only) `posterior_samples_ref`. |
| `candidate_inp_patch.json` | One row per parameter (`section`, `object`, `field_index`, `old_value`, `new_value`) — the diff to apply when the human accepts. |
| `calibration_report.md` | Human-readable summary: KGE decomposition table, secondary metrics, best parameters, convergence trace reference (SCE-UA) and posterior block (DREAM-ZS). |

The canonical INP file SHA256 is unchanged before and after calibration
— the scaffold only reads it, to extract the `old_value` for each
diff row.

Promotion is gated behind the expert-only CLI:

```bash
aiswmm calibration accept <run_dir>
```

`aiswmm calibration accept`:

1. Reads `candidate_calibration.json`; refuses if missing.
2. Reads `candidate_inp_patch.json`; refuses if missing.
3. Recomputes the SHA256 of the patch payload and compares against the
   SHA recorded inside the candidate; refuses on mismatch (tamper
   detection).
4. Applies the patch to the canonical INP via the same `inp_patch`
   machinery the agent uses.
5. Records a `human_decisions` row on the run's
   `09_audit/experiment_provenance.json` with `action ==
   "calibration_accept"`, `by == $USER`, `evidence_ref ==
   "09_audit/candidate_calibration.json"`, and `decision_text`
   containing the applied patch SHA.

The agent has no path to step 4. Only the human can promote the
candidate.

### Example: SCE-UA with candidate handover

```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py search \
  --base-inp runs/<case>/model.inp \
  --patch-map runs/<case>/calibration/patch_map.json \
  --search-space runs/<case>/calibration/search_space.json \
  --observed runs/<case>/calibration/observed_flow.csv \
  --run-root runs/<case>/calibration-sceua/trials \
  --summary-json runs/<case>/09_audit/calibration_summary.json \
  --strategy sceua --objective kge --iterations 200 --seed 42 \
  --candidate-run-dir runs/<case>

# After review:
aiswmm calibration accept runs/<case>
```

## Recommended near-term extensions
- Add multi-event calibration/validation.
- Add observed-vs-simulated overlay plots to the calibration script.
- Extend patch-map selectors beyond simple one-line object rows.
- Wire the DREAM-ZS posterior into source-decomposition uncertainty propagation — issue #55.
