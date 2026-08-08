# Climate scenario batches

`aiswmm climate` compares a model's response under precipitation-scaled
climate scenarios: a first-order climate-uplift screen that multiplies every
rainfall input by a factor per scenario, runs each scenario through the
audited SWMM runner, and writes a comparison table.

The intended loop is calibrate-then-force:

```bash
aiswmm calibrate --inp model.inp --obs observed.csv ...
aiswmm climate --inp model.inp --params-json best_params.json --patch-map patch_map.json
```

`--params-json` plus `--patch-map` apply calibrated parameter values onto the
base model first, through the same `inp_patch` contract the calibration loop
uses, so the scenario deltas are measured on the model that matches reality.

Direct use on any INP works too:

```bash
aiswmm climate --inp runs/agent/canada-fetch/05_builder/model.inp --factors "1.0,1.1,1.2,1.35"
```

The same capability is available to the agent as the `run_climate_scenarios`
tool, so a natural-language request like "compare this model under a 20%
wetter climate" runs the batch end to end.

## What it writes

Everything lands in the canonical `03_climate/` stage of the run directory:

```
03_climate/
  scenarios/<name>/model.inp     scaled model (self-contained: rain scaled,
  scenarios/<name>/*.dat         temperature/evaporation copied verbatim)
  scenarios/<name>/model.rpt     one SWMM run per scenario
  climate_summary.json           machine-readable comparison
  climate_summary.md             per-scenario table: precip, runoff,
                                 flooding, outflow, peak at the report node
```

A failed scenario keeps its row with the error attached; the batch exits
nonzero if any scenario failed.

## Scaling scope

Precipitation scaling covers inline `[TIMESERIES]` rows referenced by
`[RAINGAGES]`, raingage `FILE` sources, and `FILE`-backed timeseries
declarations. Scenario folders are self-contained, so the base model's
external files are never touched. Design-storm synthesis (Chicago, Huff,
SCS) lives in `aiswmm storm` and can generate inputs for this screen.
