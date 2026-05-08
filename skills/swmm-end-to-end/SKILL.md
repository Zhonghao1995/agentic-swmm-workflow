---
name: swmm-end-to-end
description: Top-level orchestration skill for OpenClaw-driven SWMM modelling. Use when a repository user wants one agent-facing skill that decides which module tools to run, in what order, and when to stop, for example to build, run, QA, and optionally calibrate a SWMM case from prepared or partially prepared inputs.
---

# SWMM End-to-End Orchestration

## What this skill provides
- A top-level orchestration contract for OpenClaw.
- A stable handoff point for Agentic AI project memory in `agentic-ai/memory/`.
- A deterministic execution order across the existing module skills:
  - `swmm-gis`
  - `swmm-climate`
  - `swmm-params`
  - `swmm-network`
  - `swmm-builder`
  - `swmm-runner`
  - `swmm-plot`
  - `swmm-calibration`
  - `swmm-experiment-audit`
- Clear stop conditions so the agent does not pretend a full model was built when critical inputs are still missing.
- A minimal real-data fallback path for Tod Creek via `scripts/real_cases/run_todcreek_minimal.py`.
- A mandatory audit handoff that consolidates artifacts, metrics, QA, comparison records, and default Obsidian audit notes after success or failure.

## When to use this skill
Use this skill when the user asks for:
- one OpenClaw-facing entrypoint for SWMM modelling,
- end-to-end build + run + QA,
- an agent to decide which SWMM module comes next,
- a real-data dry run before full automation is ready, or
- a bounded orchestration layer without rewriting the underlying scripts.

Do **not** use this skill when the user clearly wants only one module in isolation, such as only rainfall formatting or only calibration metrics.

## Recommended public memory preload
Before using this skill in Codex, OpenClaw, Hermes, or another compatible runtime, load the Markdown files in `agentic-ai/memory/`:

1. `identification_memory.md`
2. `soul.md`
3. `operational_memory.md`
4. `modeling_workflow_memory.md`
5. `evidence_memory.md`
6. `user_bridge_memory.md`

Those files define the public project identity, agent posture, evidence boundaries, and first-run user behavior. This skill remains the execution contract; the memory files should shape decisions and communication, not replace tool calls or depend on the maintainer's private local workspace.

## Supported operating modes
### Mode A: Full modular build
Use this when the required explicit inputs already exist or can be produced safely:
- subcatchment polygons or builder-ready `subcatchments.csv`
- network input that can become `network.json`
- land use and soil inputs
- rainfall input

Execution order:
1. `swmm-gis`
2. `swmm-params`
3. `swmm-climate`
4. `swmm-network`
5. `swmm-builder`
6. `swmm-runner`
7. QA checks
8. optional `swmm-plot`
9. optional `swmm-calibration`
10. `swmm-experiment-audit`

Exact MCP call chain for the full modular path:
1. `swmm-gis-mcp.gis_preprocess_subcatchments`
2. `swmm-params-mcp.map_landuse`
3. `swmm-params-mcp.map_soil`
4. `swmm-params-mcp.merge_params`
5. `swmm-climate-mcp.format_rainfall`
6. `swmm-climate-mcp.build_raingage_section`
7. `swmm-network-mcp.import_network` if raw network files must be imported
8. `swmm-network-mcp.qa`
9. `swmm-builder-mcp.build_inp`
10. `swmm-runner-mcp.swmm_run`
11. `swmm-runner-mcp.swmm_continuity`
12. `swmm-runner-mcp.swmm_peak`
13. optional `swmm-plot-mcp.plot_rain_runoff_si`
14. optional calibration tools:
   - `swmm-calibration-mcp.swmm_sensitivity_scan`
   - `swmm-calibration-mcp.swmm_calibrate`
   - `swmm-calibration-mcp.swmm_calibrate_search`
   - `swmm-calibration-mcp.swmm_validate`
   - `swmm-calibration-mcp.swmm_parameter_scout`
15. `swmm-experiment-audit` via CLI:
   - `python3 skills/swmm-experiment-audit/scripts/audit_run.py --run-dir runs/<case>`
   - By default this also writes the audit note into `~/Documents/Agentic-SWMM-Obsidian-Vault/20_Audit_Layer/Experiment_Audits` and updates `Experiment Audit Index.md`.

### Mode B: Prepared-input build
Use this when `subcatchments.csv`, `network.json`, params JSON, and rainfall references already exist.

Execution order:
1. `swmm-builder`
2. `swmm-runner`
3. QA checks
4. optional plotting / calibration
5. `swmm-experiment-audit`

Exact MCP call chain for prepared inputs:
1. `swmm-builder-mcp.build_inp`
2. `swmm-runner-mcp.swmm_run`
3. `swmm-runner-mcp.swmm_continuity`
4. `swmm-runner-mcp.swmm_peak`
5. optional `swmm-plot-mcp.plot_rain_runoff_si`
6. optional calibration tools
7. `swmm-experiment-audit` via CLI:
   - `python3 skills/swmm-experiment-audit/scripts/audit_run.py --run-dir runs/<case>`
   - By default this also writes the audit note into the local Obsidian audit vault and updates the audit index.

### Mode C: Minimal real-data Tod Creek fallback
Use this only when the user wants a real-data run but the full modular path is not ready because there is no trustworthy multi-subcatchment + network input yet.

Script:
- `scripts/real_cases/run_todcreek_minimal.py`

Audit command after the script returns:
- `python3 skills/swmm-experiment-audit/scripts/audit_run.py --run-dir runs/real-todcreek-minimal --workflow-mode "minimal real-data fallback"`

Characteristics:
- one subcatchment
- simple junction/outfall/conduit layout
- real DEM / land use / soil / rainfall inputs
- useful as a real-data smoke test, not as the final watershed architecture

This fallback is a script path, not a module-MCP path.
OpenClaw should choose it only when:
- the user explicitly accepts a simplified real-data smoke test, or
- full modular inputs are incomplete and the goal is to verify the repo can run with real data.

## Required decisions before execution
The orchestrator should decide these items explicitly:
- Are we doing a **full modular build** or a **minimal real-data fallback**?
- Do we already have `network.json` or equivalent network source files?
- Do we already have subcatchment polygons or builder-ready subcatchment CSV?
- Is the user asking for:
  - build only,
  - build + run + QA, or
  - build + run + QA + calibration?

If the answer is unclear, prefer:
- build + run + QA
- no calibration
- full modular build only when required inputs are real and explicit

## Input completeness rules
### For full modular build
The run should stop and report missing inputs if any of these are absent:
- network information that can be converted to `network.json`
- subcatchment geometry or builder-ready subcatchment table
- rainfall input
- land use / soil inputs or an accepted pre-merged params artifact

### For minimal Tod Creek fallback
The run requires:
- `data/Todcreek/Geolayer/n48_w124_1arc_v3_Clip_Projec1.tif`
- `data/Todcreek/Geolayer/landuse.shp`
- `data/Todcreek/Geolayer/soil.shp`
- `data/Todcreek/Rainfall/1984rain.dat`
- `data/Todcreek/outlet_candidate.geojson`

## Execution policy
- Prefer existing module scripts and MCP tools over ad hoc one-off code.
- Keep every stage artifact under `runs/<case>/...` or another explicit run directory.
- Preserve machine-readable JSON outputs where available.
- Fail fast on missing critical inputs.
- Do not silently invent a drainage network for a supposed full build.
- If full modular inputs are incomplete but Tod Creek real-data fallback is available, say so explicitly and switch only if that matches the user’s intent.
- Always call `swmm-experiment-audit` after the attempt, even when the run fails or stops early. The audit record should reflect partial evidence rather than invent missing outputs.

## Artifact handoff contract
The top-level skill should pass artifacts between MCP tools using explicit run-local paths.

Recommended stage layout:
- `runs/<case>/01_gis/subcatchments.csv`
- `runs/<case>/01_gis/subcatchments.json`
- `runs/<case>/02_params/landuse.json`
- `runs/<case>/02_params/soil.json`
- `runs/<case>/02_params/merged_params.json`
- `runs/<case>/03_climate/rainfall.json`
- `runs/<case>/03_climate/timeseries.txt`
- `runs/<case>/03_climate/raingage.json`
- `runs/<case>/03_climate/raingage.txt`
- `runs/<case>/04_network/network.json`
- `runs/<case>/04_network/network_qa.json`
- `runs/<case>/05_builder/model.inp`
- `runs/<case>/05_builder/manifest.json`
- `runs/<case>/06_runner/model.rpt`
- `runs/<case>/06_runner/model.out`
- `runs/<case>/06_runner/manifest.json`
- `runs/<case>/07_qa/continuity.json`
- `runs/<case>/07_qa/peak.json`
- optional `runs/<case>/08_plot/...`
- optional `runs/<case>/09_calibration/...`
- `runs/<case>/experiment_provenance.json`
- `runs/<case>/comparison.json`
- `runs/<case>/experiment_note.md`

## MCP execution notes
### GIS stage
- Use `gis_preprocess_subcatchments` only when a subcatchment polygon dataset already exists.
- Do not claim GIS preprocessing can replace watershed delineation or pipe-network generation.

### Params stage
- `map_landuse` and `map_soil` should target the same subcatchment ID universe.
- `merge_params` should be treated as the single params handoff into `build_inp`.

### Climate stage
- `format_rainfall` should create both JSON metadata and timeseries text.
- `build_raingage_section` should run after rainfall formatting so `series_name` or `rainfall_json` stays consistent.

### Network stage
- Use `import_network` only when raw conduits/junctions/outfalls exist.
- If `network.json` already exists, skip import and run `qa` directly.
- If no trustworthy network source exists, stop the full modular path.

### Builder stage
- `build_inp` is the only handoff into `swmm_run`.
- Treat builder validation failures as hard stops for the full modular path.

### Runner and QA stage
- `swmm_run` creates the canonical `manifest.json`.
- `swmm_continuity` and `swmm_peak` are mandatory QA steps, not optional metrics.
- Prefer the fixed `swmm_peak` parser path that reads the correct summary block.

### Calibration stage
- Do not enter calibration unless the user requested it or the workflow explicitly includes it.
- Require an observed flow file before any calibration tool is called.

### Audit stage
- Run `swmm-experiment-audit` after success, failure, or early stop.
- Use the run directory as the single audit input.
- Pass `--compare-to <baseline-run-dir>` when the user requests baseline/scenario or before/after comparison.
- The audit must write `experiment_provenance.json`, `comparison.json`, and Obsidian-compatible `experiment_note.md`.
- The audit should also use the default Obsidian export unless the user explicitly asks for `--no-obsidian`.
- The default Obsidian vault is `~/Documents/Agentic-SWMM-Obsidian-Vault`, with `10_Memory_Layer` for durable lessons and `20_Audit_Layer` for run-level evidence.
- Do not include chat transcripts or conversational content in audit outputs.

## OpenClaw prompt-level instruction
When OpenClaw uses this skill, it should:
- choose one operating mode first,
- announce the chosen mode,
- create a case run directory,
- call only the MCP tools required for that mode,
- stop immediately on missing critical inputs instead of hallucinating replacements,
- call `swmm-experiment-audit` on the run directory,
- summarize which concrete artifacts and audit records were produced.

## QA gates
Minimum QA checks for a successful run:
- builder validation has no critical missing sections
- SWMM return code is zero
- `.rpt` and `.out` exist
- continuity metrics can be parsed
- peak metric can be parsed from the correct summary block

For calibration mode, also require:
- observed flow file parses successfully
- time overlap between observed and simulated series is adequate

## Output contract
At minimum, the orchestrator should leave behind:
- built `.inp`
- run `.rpt`
- run `.out`
- `manifest.json`
- a short machine-readable QA summary
- `experiment_provenance.json`
- `comparison.json`
- Obsidian-compatible `experiment_note.md`

If plotting is requested, also produce:
- rainfall-runoff figure artifact

If calibration is requested, also produce:
- ranking / summary JSON
- chosen parameter set or best-params output

## Recommended OpenClaw behavior
- Use this skill as the **only top-level SWMM skill**.
- Treat module skills as subordinate implementation skills.
- Keep reasoning at the orchestration layer and calculations at the script layer.
- When a run fails, report the failing stage and the missing or invalid input rather than guessing.

## Current limitations
- This skill does not remove the need for `swmm-network`; it only coordinates it.
- Full watershed automation still depends on real subcatchment + network preparation.
- The Tod Creek real-data fallback is intentionally simplified and should not be confused with the final multi-subcatchment production workflow.
