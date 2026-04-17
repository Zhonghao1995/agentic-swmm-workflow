# OpenClaw Execution Path

This document defines the intended top-level execution path for `swmm-end-to-end`.

It is not a new MCP server. It is the concrete tool-call contract that OpenClaw should follow when using the existing module MCP servers.

## Goal

Use one top-level OpenClaw skill to call the existing SWMM module tools in a stable order with explicit artifact handoff.

## Top-level principle

- Keep reasoning in OpenClaw.
- Keep calculations in Python scripts behind MCP tools.
- Keep artifacts in a run-local directory.
- Stop on missing critical inputs instead of fabricating them.

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

Inputs:
- subcatchment polygon dataset
- `network.json`

Outputs:
- `runs/<case>/01_gis/subcatchments.csv`
- `runs/<case>/01_gis/subcatchments.json`

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

## What should come next

The next implementation step is not a brand-new MCP server by default.

The next step is to connect OpenClaw prompts and runtime behavior to this exact execution path so `swmm-end-to-end` becomes operational rather than only descriptive.
