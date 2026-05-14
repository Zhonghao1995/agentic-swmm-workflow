# OpenClaw Execution Path

This document defines the intended top-level execution path for `swmm-end-to-end` when used by Codex, OpenClaw, Hermes, or another MCP-centered external agent runtime.

It is not a new MCP server. It is the concrete tool-call contract that an agent runtime should follow when using the existing module MCP servers.

For Codex as a local development and audit runtime, see `docs/codex-runtime.md`. Codex can follow the same skill contract locally, but it can also edit code, run scripts, inspect generated files, update Obsidian, and verify diffs inside the checkout.

## Goal

Use one top-level Agentic SWMM skill to call the existing SWMM module tools in a stable order with explicit artifact handoff.

## Top-level principle

- Keep reasoning in the agent runtime.
- Keep calculations in Python scripts behind MCP tools.
- Keep artifacts in a run-local directory.
- Stop on missing critical inputs instead of fabricating them.
- Always run the experiment audit layer after success, failure, or early stop.

## Memory layer

Before loading the `swmm-end-to-end` skill, Codex, OpenClaw, or Hermes should load the project memory files in:

```text
agentic-ai/memory/
```

Recommended order:

1. `agentic-ai/memory/identification_memory.md`
2. `agentic-ai/memory/soul.md`
3. `agentic-ai/memory/operational_memory.md`
4. `agentic-ai/memory/modeling_workflow_memory.md`
5. `agentic-ai/memory/evidence_memory.md`
6. `agentic-ai/memory/user_bridge_memory.md`

This memory layer gives a public Agentic AI runtime user stable project identity, modelling posture, evidence boundaries, and first-run behavior before the agent starts calling SWMM module tools. It is not a new runtime, does not depend on the maintainer's private local workspace, and should not bypass this execution path.

## Full modular path

Use this only when the case has all required input classes:
- subcatchment geometry or builder-ready subcatchments table
- network source or `network.json`
- land use input
- soil input
- rainfall input

Recommended run directory:

`runs/<case>/`

### Stage 1: GIS

Tool:
- `swmm-gis-mcp.gis_preprocess_subcatchments`
- `swmm-gis-mcp.qgis_normalize_layers` when DEM / land use / soil / boundary layers need CRS harmonization and boundary clipping before hydrology
- `swmm-gis-mcp.qgis_export_swmm_intermediates` when starting from QGIS-prepared raw GIS layers
- `swmm-gis-mcp.qgis_raw_to_entropy_partition` when starting from raw DEM / land use / soil / boundary layers and using QGIS/GRASS hydrology plus paper-rule entropy partitioning
- `swmm-gis-mcp.qgis_todcreek_raw_to_entropy_partition` as a Tod Creek case-study alias for regression and reproducibility checks

Inputs:
- subcatchment polygon dataset
- `network.json`
- optional QGIS/raw GIS sources: DEM, land use layer, soil layer, outlet, rainfall, drainage assets

Outputs:
- `runs/<case>/01_gis/subcatchments.csv`
- `runs/<case>/01_gis/subcatchments.json`
- QGIS bridge outputs, when used:
  - `runs/<case>/00_raw/qgis_layers_manifest.json`
  - `runs/<case>/00_raw/qgis_crs_report.json`
  - `runs/<case>/01_gis/threshold_sweep/{acc,drain,basin,stream}_100.tif`
  - `runs/<case>/02_params/paper_entropy_partition/`
  - `runs/<case>/02_params/threshold_sensitivity/`
  - `runs/<case>/07_figures/paper_rule_decision_spaces_5panel.png`
  - `runs/<case>/07_figures/paper_rule_watershed_partitions_5panel.png`
  - `runs/<case>/audit/qgis_entropy_run_manifest.json`
  - `runs/<case>/02_params/landuse.csv`
  - `runs/<case>/02_params/soil.csv`
  - `runs/<case>/04_network/network_qa.json`

Boundary:
- Prepared-overlay mode expects QGIS to provide delineated/overlayed subcatchment polygons and exports SWMM-ready intermediates.
- Entropy-partition mode calls QGIS Processing / GRASS hydrology, computes paper-consistent WJE/NWJE/WFJS split-lump diagnostics, and writes audit artifacts for subcatchment spatial-unit selection. This is still GIS preprocessing evidence, not calibrated SWMM hydrologic validation.

### Stage 2: Params

Tools:
- `swmm-params-mcp.map_landuse`
- `swmm-params-mcp.map_soil`
- `swmm-params-mcp.merge_params`

Inputs:
- land use CSV keyed by `subcatchment_id`
- soil CSV keyed by `subcatchment_id`

Outputs:
- `runs/<case>/02_params/landuse.json`
- `runs/<case>/02_params/soil.json`
- `runs/<case>/02_params/merged_params.json`

### Stage 3: Climate

Tools:
- `swmm-climate-mcp.format_rainfall`
- `swmm-climate-mcp.build_raingage_section`

Outputs:
- `runs/<case>/03_climate/rainfall.json`
- `runs/<case>/03_climate/timeseries.txt`
- `runs/<case>/03_climate/raingage.json`
- `runs/<case>/03_climate/raingage.txt`

### Stage 4: Network

Tools:
- `swmm-network-mcp.import_network` when starting from raw conduit/junction/outfall files
- `swmm-network-mcp.qa`
- optional `swmm-network-mcp.summary`

Outputs:
- `runs/<case>/04_network/network.json`
- `runs/<case>/04_network/network_qa.json`

If no valid network source exists, stop here. Do not fabricate a network in the full modular path.

### Stage 5: Builder

Tool:
- `swmm-builder-mcp.build_inp`

Outputs:
- `runs/<case>/05_builder/model.inp`
- `runs/<case>/05_builder/manifest.json`

### Stage 6: Runner

Tool:
- `swmm-runner-mcp.swmm_run`

Outputs:
- `runs/<case>/06_runner/model.rpt`
- `runs/<case>/06_runner/model.out`
- `runs/<case>/06_runner/manifest.json`

### Stage 7: QA

Tools:
- `swmm-runner-mcp.swmm_continuity`
- `swmm-runner-mcp.swmm_peak`

Outputs:
- `runs/<case>/07_qa/continuity.json`
- `runs/<case>/07_qa/peak.json`

Minimum pass conditions:
- builder validation is clean enough to proceed
- SWMM return code is zero
- `.rpt` and `.out` exist
- continuity parses
- peak parses from the correct summary block

### Stage 8: Optional plot

Tool:
- `swmm-plot-mcp.plot_rain_runoff_si`

### Stage 9: Optional calibration

Tools:
- `swmm-calibration-mcp.swmm_sensitivity_scan`
- `swmm-calibration-mcp.swmm_calibrate`
- `swmm-calibration-mcp.swmm_calibrate_search`
- `swmm-calibration-mcp.swmm_validate`
- `swmm-calibration-mcp.swmm_parameter_scout`

Calibration preconditions:
- observed flow file exists
- observed flow parses
- user explicitly requested calibration or the workflow includes it

### Stage 9b: Optional uncertainty propagation

Current implementation:
- `skills/swmm-uncertainty/scripts/uncertainty_propagate.py`
- `skills/swmm-uncertainty/scripts/probabilistic_sampling.py`
- `skills/swmm-uncertainty/scripts/parameter_recommender.py`
- `skills/swmm-uncertainty/scripts/monte_carlo_propagate.py`
- `skills/swmm-uncertainty/scripts/entropy_metrics.py`

Future MCP wrapper:
- `swmm-uncertainty-mcp.swmm_uncertainty_run`

Inputs:
- base SWMM INP
- calibration-style `patch_map.json`
- user-defined `fuzzy_space.json`
- optional `monte_carlo_space.json`
- `uncertainty_config.json`

Outputs:
- `runs/<case>/09_uncertainty/fuzzy_space.resolved.json`
- `runs/<case>/09_uncertainty/alpha_intervals.json`
- `runs/<case>/09_uncertainty/parameter_sets.json`
- `runs/<case>/09_uncertainty/uncertainty_summary.json`
- optional output-ensemble entropy summaries
- optional node-level entropy curve figures

Use this stage when:
- the user explicitly requests uncertainty propagation,
- the workflow needs to quantify epistemic parameter uncertainty,
- membership functions are defined as triangular or trapezoidal fuzzy numbers,
- Monte Carlo parameter distributions are defined for prior or calibration-informed uncertainty analysis.

Do not call this stage calibration unless observed data are available and `swmm-calibration` computes simulation-vs-observation metrics.

The default compact triangular interpretation is:
- `lower` and `upper` come from the user,
- the current model value is resolved from the base INP and used as the triangle peak,
- the baseline must lie inside `[lower, upper]`.

### Stage 9c: Optional LID design scenarios

Current implementation:
- `skills/swmm-lid-optimization/scripts/lid_scenario_builder.py`
- `skills/swmm-lid-optimization/scripts/entropy_lid_priority.py`
- `scripts/benchmarks/run_tecnopolo_lid_placement_smoke.py`

Future MCP wrapper:
- `swmm-lid-optimization-mcp.swmm_lid_scenarios`

Inputs:
- base SWMM INP
- LID control definitions
- placement rules and candidate filters
- optional priority table from D8 / normalized joint entropy / fuzzy similarity diagnostics
- objective preferences such as peak-flow reduction, runoff-volume reduction, cost proxy, or Pareto ranking

Outputs:
- generated scenario INPs
- `scenario_manifest.json` for each candidate
- `summary.json` for benchmark scoring
- area-normalized metrics such as peak reduction per unit LID area when benchmark scoring is used
- optional baseline-vs-candidate hydrograph figure

Use this stage when:
- the user explicitly asks for LID, green infrastructure, placement, combination, or design optimization,
- a runnable base SWMM model exists,
- design constraints are available or can be stated as assumptions.

Do not call this stage final optimization until the candidate generator, SWMM runs, objective scoring, and audit outputs are all available.

For fair placement-strategy comparison, keep the total LID area or budget fixed across strategies where possible. Report both total reduction and unit-area or cost-normalized reduction so the result is not driven only by adding more LID area.

### Stage 10: Experiment audit

Tool:
- `swmm-experiment-audit` CLI

Command:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py --run-dir runs/<case>
```

With a comparison target:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/<case> \
  --compare-to runs/<baseline-case>
```

Outputs:
- `runs/<case>/experiment_provenance.json`
- `runs/<case>/comparison.json`
- `runs/<case>/experiment_note.md`

Run this stage even when an earlier stage fails or stops early. The audit record should preserve partial evidence and missing artifacts instead of fabricating a complete run.

## Prepared-input path

Use this when the case already has:
- `subcatchments.csv`
- `network.json`
- merged params JSON
- rainfall JSON and/or timeseries/raingage artifacts

Call order:
1. `swmm-builder-mcp.build_inp`
2. `swmm-runner-mcp.swmm_run`
3. `swmm-runner-mcp.swmm_continuity`
4. `swmm-runner-mcp.swmm_peak`
5. optional plotting
6. optional calibration
7. optional fuzzy uncertainty propagation
8. `swmm-experiment-audit` CLI

## Tod Creek minimal real-data fallback

This path is intentionally not the same as the full modular path.

Script:
- `scripts/real_cases/run_todcreek_minimal.py`

Use it only when:
- the goal is to verify real-data execution inside this repo, and
- a full multi-subcatchment + network path is not yet ready.

This fallback currently uses:
- copied real Tod Creek DEM
- copied land use and soil shapefiles
- copied rainfall data
- copied outlet point
- simplified one-subcatchment + one-conduit topology

After running the fallback script, audit it with:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/real-todcreek-minimal \
  --workflow-mode "minimal real-data fallback"
```

## What should come next

The next implementation step is to connect OpenClaw prompts and runtime behavior to this exact execution path so `swmm-end-to-end` consistently runs the audit layer after every build/run/QA attempt.
