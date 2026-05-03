---
name: swmm-uncertainty
description: Fuzzy alpha-cut uncertainty propagation for EPA SWMM. Use when Zhonghao asks to define triangular/trapezoidal membership functions for SWMM parameters, propagate epistemic parameter uncertainty through SWMM, or summarize output uncertainty envelopes.
---

# SWMM Fuzzy Uncertainty

## What this skill provides

- User-defined fuzzy membership functions for SWMM parameters.
- Baseline-aware triangular fuzzy numbers, where the current model value is the default triangle peak.
- Alpha-cut transformation from fuzzy membership functions to parameter intervals.
- LHS, random, or boundary sampling inside each alpha-cut interval.
- Batch propagation through SWMM by reusing the existing calibration patch-map convention.
- Machine-readable uncertainty summaries for output envelopes and failed/invalid samples.

This skill is intentionally separate from `swmm-calibration`.

- `swmm-calibration` asks: which parameter set best matches observations?
- `swmm-uncertainty` asks: how much output uncertainty is induced by user-defined parameter uncertainty?

## Scripts

- `scripts/fuzzy_membership.py`
  - parses and validates crisp, interval, triangular, and trapezoidal fuzzy parameter specs
  - resolves `baseline: "from_model"` from the base INP through the patch map
  - computes alpha-cut intervals
- `scripts/sampling.py`
  - generates parameter sets from alpha-cut intervals
  - supports `lhs`, `random`, and `boundary`
- `scripts/uncertainty_propagate.py`
  - main CLI entry point
  - writes resolved fuzzy space, alpha intervals, parameter sets, trial INPs, and summary JSON
  - optionally executes SWMM and aggregates peak/continuity envelopes

## Expected Workflow

1. Prepare a base SWMM INP.
2. Prepare a calibration-style `patch_map.json`.
3. Define a `fuzzy_space.json`.
4. Define an `uncertainty_config.json`.
5. Run `uncertainty_propagate.py`.
6. Inspect `uncertainty_summary.json`, `alpha_intervals.json`, and generated trial directories.

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

## MVP Assumptions

- The first implementation focuses on epistemic parameter uncertainty, not probability distributions.
- The current model value is treated as the most plausible value for compact triangular specs.
- Real SWMM propagation depends on `swmm5` being installed.
- Hydrograph goodness-of-fit metrics remain in `swmm-calibration`; this skill can be extended later to call that observed-flow path.
