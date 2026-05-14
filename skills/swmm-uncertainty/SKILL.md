---
name: swmm-uncertainty
description: Parameter and forcing uncertainty propagation and sensitivity analysis for EPA SWMM. Use when an agent needs to (1) propagate parameter uncertainty through SWMM (fuzzy alpha-cut or Monte Carlo), (2) quantify hydrograph envelopes or output entropy without treating the run as calibration, (3) screen which parameters matter using OAT / Morris elementary-effects / Sobol' indices, or (4) generate a rainfall ensemble (observed-series perturbation or IDF-curve design storms) and aggregate the resulting hydrograph envelope.
---

# SWMM Uncertainty

## What this skill provides

- User-defined fuzzy membership functions for SWMM parameters.
- Baseline-aware triangular fuzzy numbers, where the current model value is the default triangle peak.
- Alpha-cut transformation from fuzzy membership functions to parameter intervals.
- LHS, random, or boundary sampling inside each alpha-cut interval.
- Monte Carlo parameter sampling for prior or calibration-informed probability distributions.
- Normal/lognormal/truncated-normal/uniform sampling with simple physical constraints such as bound parameters and greater-than rules.
- Batch propagation through SWMM by reusing the existing calibration patch-map convention.
- Normalized Shannon entropy metrics for output ensembles, such as hydrograph entropy over time.
- Machine-readable uncertainty summaries for output envelopes, entropy records, and failed/invalid samples.
- Sensitivity-analysis screening with three sub-methods (OAT / Morris / Sobol') sharing one entry point (`scripts/sensitivity.py`).
- Rainfall-forcing ensembles: time-series perturbation of an observed rainfall record (gaussian, multiplicative, AR(1), intensity_scaling) or IDF-curve sampling of design storms (Chicago / Huff / SCS Type II), with optional per-realisation SWMM runs and ensemble envelope aggregation.

This skill is intentionally separate from `swmm-calibration`.

- `swmm-calibration` asks: which parameter set best matches observations?
- `swmm-uncertainty` asks: how much output uncertainty is induced by user-defined parameter uncertainty, and which parameters drive that uncertainty?

Calibration requires observed data and performance metrics such as NSE, RMSE, or KGE. This skill can run without observed data when the task is prior uncertainty propagation. The sensitivity-analysis path *does* read an observed series (it scores trials by RMSE against observed flow), but it answers a different question from calibration: "which parameter spread matters?" rather than "which single set is best?". When calibration outputs exist, they can be used to narrow Monte Carlo ranges or define posterior-like parameter sets.

## Scripts

- `scripts/fuzzy_membership.py`
  - parses and validates crisp, interval, triangular, and trapezoidal fuzzy parameter specs
  - resolves `baseline: "from_model"` from the base INP through the patch map
  - computes alpha-cut intervals
- `scripts/sampling.py`
  - generates parameter sets from alpha-cut intervals
  - supports `lhs`, `random`, and `boundary`
- `scripts/probabilistic_sampling.py`
  - generates Monte Carlo parameter sets from probability distributions
  - supports `uniform`, `normal`, `truncnorm`, and `lognormal`
  - supports simple constraints such as `bind`, `greater_than`, and `less_than`
- `scripts/parameter_recommender.py`
  - inspects an INP and recommends prior Monte Carlo parameters that are actually present in the model
  - reports the evidence boundary so prior ranges are not mistaken for calibrated posterior ranges
- `scripts/monte_carlo_propagate.py`
  - extracts node-flow ensembles from Monte Carlo trial `.out` files
  - calls `entropy_metrics.py` to produce node entropy JSON records
  - plots normalized output entropy curves for selected nodes
- `scripts/entropy_metrics.py`
  - calculates normalized discrete Shannon entropy for output ensembles
  - summarizes ensemble p05/p50/p95/min/max time series
- `scripts/uncertainty_propagate.py`
  - main CLI entry point
  - writes resolved fuzzy space, alpha intervals, parameter sets, trial INPs, and summary JSON
  - optionally executes SWMM and aggregates peak/continuity envelopes
- `scripts/sensitivity.py`
  - unified sensitivity-analysis entry point with three sub-methods
    - `--method oat`: one-at-a-time perturbation around a baseline (port of the legacy `parameter_scout`)
    - `--method morris`: Morris elementary-effects via SALib; sample budget `r * (k + 1)`; reports `mu_star` and `sigma` per parameter
    - `--method sobol`: Sobol' indices via SALib (Saltelli sampling); sample budget `N * (2k + 2)`; reports first-order `S_i` and total-effect `S_T_i`
  - writes a `sensitivity_indices.json` summary (typically under `runs/<case>/09_audit/`)
  - the Morris and Sobol' paths require SALib (declared in `pyproject.toml`)
- `scripts/rainfall_ensemble.py`
  - rainfall ensemble generator with two methods
    - `--method perturbation`: noisy realisations of an observed rainfall timeseries (CSV or SWMM `.dat`). Models: `gaussian_iid`, `multiplicative`, `autocorrelated` (AR(1)), `intensity_scaling`. Flag `preserve_total_volume` rescales each realisation to match the observed total when set
    - `--method idf`: synthesised hyetographs from IDF parameters `(a, b, c)` with confidence intervals. Storm types: `chicago` (Keifer-Chu), `huff` (4 quartiles), `scs_type_ii` (canonical 24-hr Type II)
  - if `--base-inp` is supplied, every realisation is patched into the base INP's `[TIMESERIES]` block and run through swmm5; peak flow + total outfall volume at `--swmm-node` are aggregated into `swmm_ensemble_stats`
  - writes per-realisation CSVs under `<run-root>/09_audit/rainfall_realisations/` and a v1 summary at `<run-root>/09_audit/rainfall_ensemble_summary.json`

## Sensitivity-analysis sub-modes

The three modes share the patch-map workflow and the `--observed` series so that trials can be scored by RMSE against the same target flow node.

| Sub-method | Config input              | Sample budget                | Output indices         |
|------------|---------------------------|------------------------------|------------------------|
| `oat`      | `base_params.json` + `scan_spec.json` (parameter -> list of trial values) | `sum_i len(scan_spec[i])` | `importance`, `recommended_direction`, `suggested_next_range` |
| `morris`   | `parameter_space.json` (parameter -> `{min, max}`) | `r * (k + 1)`, `r = --morris-r` | `mu`, `mu_star`, `sigma`, `mu_star_conf` |
| `sobol`    | `parameter_space.json` (parameter -> `{min, max}`) | `N * (2k + 2)`, `N = --sobol-n`, `calc_second_order=True` | `S_i` (first-order), `S_T_i` (total-effect), 95% conf |

OAT is the cheapest, Morris is the standard screening method, and Sobol' decomposes variance into first-order and total-effect contributions (more expensive but more informative).

## Expected fuzzy workflow

1. Prepare a base SWMM INP.
2. Prepare a calibration-style `patch_map.json`.
3. Define a `fuzzy_space.json`.
4. Define an `uncertainty_config.json`.
5. Run `uncertainty_propagate.py`.
6. Inspect `uncertainty_summary.json`, `alpha_intervals.json`, and generated trial directories.

## Expected Monte Carlo / entropy workflow

1. Prepare a base SWMM INP.
2. Prepare a calibration-style `patch_map.json`.
3. Define a `monte_carlo_space.json` with parameter distributions.
4. Generate parameter sets with `probabilistic_sampling.py`.
5. Propagate the generated parameter sets through SWMM using the uncertainty runner path.
6. Extract an output ensemble, such as `node,OUT_0,Total_inflow`.
7. Calculate normalized output entropy with `entropy_metrics.py`.

If observed data are available, first run `swmm-calibration` and use its best, acceptable, or narrowed parameter ranges as a calibration-informed Monte Carlo input. If observed data are not available, report the analysis as prior uncertainty propagation.

## Fuzzy Space

For a triangular membership function, the preferred compact form is:

```json
{
  "parameters": {
    "pct_imperv_s1": {
      "type": "triangular",
      "lower": 15.0,
      "upper": 40.0,
      "baseline": "from_model"
    }
  }
}
```

The resolved triangle is:

```text
triangular(a=lower, b=current model value, c=upper)
```

The baseline must lie inside `[lower, upper]`; otherwise the configuration is invalid.

A trapezoidal function can be fully specified:

```json
{
  "parameters": {
    "n_imperv_s1": {
      "type": "trapezoidal",
      "lower": 0.010,
      "core_lower": 0.013,
      "core_upper": 0.018,
      "upper": 0.025
    }
  }
}
```

Or centered around the baseline:

```json
{
  "parameters": {
    "n_imperv_s1": {
      "type": "trapezoidal",
      "lower": 0.010,
      "upper": 0.025,
      "core_width": 0.004,
      "baseline": "from_model"
    }
  }
}
```

## CLI Example

```bash
python3 skills/swmm-uncertainty/scripts/uncertainty_propagate.py \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --fuzzy-space skills/swmm-uncertainty/examples/fuzzy_space.json \
  --config skills/swmm-uncertainty/examples/uncertainty_config.json \
  --run-root runs/uncertainty-demo \
  --summary-json runs/uncertainty-demo/uncertainty_summary.json \
  --dry-run
```

Remove `--dry-run` to execute SWMM for every generated trial.

### Monte Carlo sampling example

```bash
python3 skills/swmm-uncertainty/scripts/probabilistic_sampling.py \
  --parameter-space skills/swmm-uncertainty/examples/monte_carlo_space.json \
  --samples 100 \
  --seed 42 \
  --out runs/uncertainty-mc/parameter_sets.json
```

### Entropy metric example

```bash
python3 skills/swmm-uncertainty/scripts/entropy_metrics.py \
  --ensemble-json skills/swmm-uncertainty/examples/entropy_ensemble.json \
  --bins 10 \
  --out runs/uncertainty-mc/entropy_summary.json
```

### Tecnopolo Monte Carlo smoke example

```bash
python3 scripts/benchmarks/run_tecnopolo_mc_uncertainty_smoke.py \
  --samples 20 \
  --seed 42 \
  --node OUT_0 \
  --scan-nodes \
  --entropy-nodes J6 OUT_0
```

This is a prior uncertainty smoke test, not calibration. It identifies perturbable parameters in the Tecnopolo HORTON prepared INP, applies small Monte Carlo perturbations, runs SWMM, optionally ranks all junction/outfall nodes by peak-flow spread, and writes `summary.json`, `parameter_recommendations.json`, trial outputs, a rainfall-plus-flow envelope figure, J6/OUT_0 entropy JSON files, and an entropy curve figure under `runs/benchmarks/tecnopolo-mc-uncertainty-smoke/`.

### Sensitivity-analysis examples

OAT (port of the legacy `parameter_scout`):

```bash
python3 skills/swmm-uncertainty/scripts/sensitivity.py \
  --method oat \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --base-params examples/calibration/base_params.json \
  --scan-spec examples/calibration/scan_spec.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/sensitivity-oat \
  --summary-json runs/sensitivity-oat/09_audit/sensitivity_indices.json \
  --swmm-node O1
```

Morris elementary-effects (`r=10` trajectories on a 4-parameter space gives 50 swmm5 calls):

```bash
python3 skills/swmm-uncertainty/scripts/sensitivity.py \
  --method morris \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --parameter-space examples/calibration/search_space.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/sensitivity-morris \
  --summary-json runs/sensitivity-morris/09_audit/sensitivity_indices.json \
  --morris-r 10 \
  --seed 42
```

Sobol' indices (`N=64` on a 4-parameter space gives 640 swmm5 calls; budget is `N*(2k+2)`):

```bash
python3 skills/swmm-uncertainty/scripts/sensitivity.py \
  --method sobol \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --parameter-space examples/calibration/search_space.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/sensitivity-sobol \
  --summary-json runs/sensitivity-sobol/09_audit/sensitivity_indices.json \
  --sobol-n 64 \
  --seed 42
```

All three modes share the same `--summary-json` schema header (`method`, `parameters`, `sample_budget`, `indices`). Per-parameter shapes differ by method (see the "Sensitivity-analysis sub-modes" table above).

### Rainfall ensemble examples

Time-series perturbation (200 noisy realisations of an observed rainfall CSV, all run through swmm5):

```bash
python3 skills/swmm-uncertainty/scripts/rainfall_ensemble.py \
  --method perturbation \
  --config skills/swmm-uncertainty/examples/rainfall_perturbation_config.json \
  --run-root runs/rainfall-ensemble-perturbation \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --series-name TS_RAIN \
  --swmm-node O1 \
  --seed 42
```

IDF-curve design storm (200 hyetographs from sampled Chicago IDF params):

```bash
python3 skills/swmm-uncertainty/scripts/rainfall_ensemble.py \
  --method idf \
  --config skills/swmm-uncertainty/examples/rainfall_idf_config.json \
  --run-root runs/rainfall-ensemble-idf \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --series-name TS_RAIN \
  --swmm-node O1 \
  --seed 42
```

Use `--dry-run` to skip the SWMM execution layer and write only the realisation CSVs + rainfall-only summary statistics.

### Rainfall ensemble — methods at a glance

| Method | Input | Models | Output |
|--------|-------|--------|--------|
| `perturbation` | One observed rainfall CSV / SWMM `.dat` | `gaussian_iid`, `multiplicative`, `autocorrelated`, `intensity_scaling` | `N` realisations of the observed pattern |
| `idf` | IDF `(a, b, c)` with CIs + storm type | `chicago`, `huff` (4 quartiles), `scs_type_ii` | `N` synthesised hyetographs |

`gaussian_iid` adds zero-mean Gaussian noise (mean residual ≈ 0). `multiplicative` preserves the shape — Pearson correlation between observed and any realisation stays near 1. `autocorrelated` produces noise with lag-1 autocorrelation ≈ `ar1_coefficient`. `intensity_scaling` scales noise variance with intensity, so peaks fluctuate more than troughs.

When `preserve_total_volume=true`, every realisation is rescaled so its integrated rainfall depth matches the observed total. When `false`, totals vary across the ensemble — that variance is itself part of the propagated uncertainty.

## Outputs

The run directory contains:

- `fuzzy_space.resolved.json`
- `alpha_intervals.json`
- `parameter_sets.json`
- `trials/<trial>/model.inp`
- `trials/<trial>/manifest.json` when SWMM execution is enabled
- `uncertainty_summary.json`

The summary answers:

- What parameter interval was used at each alpha level?
- What samples were propagated?
- How many trials succeeded, failed, or were only dry-run trials?
- What peak-flow and continuity envelopes were induced by each alpha level?
- What output entropy curve was induced by the propagated ensemble?

## MVP Assumptions

- Fuzzy analysis focuses on epistemic parameter uncertainty through membership functions.
- Monte Carlo analysis supports prior or calibration-informed probability distributions.
- The current model value is treated as the most plausible value for compact triangular specs.
- Entropy is calculated from SWMM output ensembles; it is parameter-induced output entropy, not parameter entropy and not calibration performance.
- Real SWMM propagation depends on `swmm5` being installed.
- Hydrograph goodness-of-fit metrics remain in `swmm-calibration`; this skill can be extended later to call that observed-flow path.
