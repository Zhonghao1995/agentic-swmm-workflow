---
name: swmm-end-to-end
description: Top-level orchestration skill for OpenClaw-driven SWMM modelling. Use when a repository user wants one agent-facing skill that decides which module tools to run, in what order, and when to stop, for example to build, run, QA, and optionally calibrate a SWMM case from prepared or partially prepared inputs.
---

# SWMM End-to-End Orchestration

## What this skill provides
- A top-level orchestration contract for OpenClaw.
- A stable handoff point for Agentic AI project memory in `agent/memory/`.
- A deterministic execution order across the existing module skills:
  - `swmm-gis`
  - `swmm-climate`
  - `swmm-params`
  - `swmm-network`
  - `swmm-builder`
  - `swmm-runner`
  - `swmm-plot`
  - `swmm-calibration`
  - `swmm-uncertainty`
  - `swmm-lid-optimization`
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
Before using this skill in Codex, OpenClaw, Hermes, or another compatible runtime, load the Markdown files in `agent/memory/`:

1. `identification_memory.md`
2. `soul.md`
3. `operational_memory.md`
4. `modeling_workflow_memory.md`
5. `evidence_memory.md`
6. `user_bridge_memory.md`

Those files define the public project identity, agent posture, evidence boundaries, and first-run user behavior. This skill remains the execution contract; the memory files should shape decisions and communication, not replace tool calls or depend on the maintainer's private local workspace.

## Supported operating modes
### Mode 0: MCP-first framework smoke test mode
Use this when the user is testing the Agentic SWMM framework itself, especially with prompts like "test the end-to-end framework", "test skill and MCP tool calls", "from raw data to INP", or "automatic modeling smoke test".

This mode tests orchestration behavior before scientific model quality. It must:
- trigger this `swmm-end-to-end` skill first;
- choose the smallest real-data subset that can exercise the chain;
- call the relevant MCP tool contracts as the primary path;
- Do not bypass MCP tool contracts by calling the underlying Python scripts as the primary path;
- use temporary script edits only for missing framework adapters that do not yet have MCP coverage;
- record every temporary script fallback as missing framework capability, not as a completed MCP feature.

The run manifest for this mode must include:
- `tool_transport`: `mcp` when the MCP server was called through the protocol, `script_fallback` when the tool contract exists but the MCP transport was bypassed, or `temporary_script` when no MCP contract exists yet;
- `mcp_tool_calls`: ordered records of the intended or actual tool calls, including server, tool name, input paths, output paths, and status;
- `missing_or_fallback_inputs`: explicit data gaps and assumptions such as missing soil, nonnumeric pipe diameter fallback, inferred outfall, inferred invert elevation, or shortened rainfall window;
- `framework_gaps`: MCP/skill gaps discovered during the run that should be implemented later.

Use `scripts/mcp_stdio_call.py` when Codex needs to verify the MCP transport directly from this repository. The helper initializes a server over stdio, checks `tools/list`, calls one tool with JSON arguments, and stores the raw MCP response as an artifact. Use repo-root relative paths in the JSON arguments; the helper resolves those to absolute paths before sending the tool call so server-local working directories do not corrupt path resolution.

For a raw-data to INP smoke test, prefer this MCP call order when inputs are present:
1. `swmm-gis-mcp.qgis_area_weighted_params` for land-use/soil area-weighted params, or record a missing-input fallback if soil is absent.
2. `swmm-climate-mcp.format_rainfall` for event rainfall.
3. `swmm-climate-mcp.build_raingage_section` when an explicit raingage artifact is needed.
4a. `swmm-network-mcp.prepare_storm_inputs` for raw municipal shapefiles (clip pipes + manholes to basin, fill mapping from `skills/swmm-network/templates/city_mapping_raw_shapefile.template.json`). Skip when the source is already a structured CAD export with explicit from/to nodes.
4b. `swmm-network-mcp.snap_pipe_endpoints` — heal sub-millimetre to centimetre vertex drift between adjacent pipe segments. Reasonable starting tolerance is 0.5–3 m. Reports clusters merged + any pipes dropped as self-loops.
4c. `swmm-network-mcp.infer_outfall` (mode `endpoint_nearest_watercourse` when a watercourse layer is available; else `lowest_endpoint`).
4d. `swmm-network-mcp.reorient_pipes` to BFS-flip flow direction.
4e. `swmm-network-mcp.import_city_network` to assemble `network.json`.
5. `swmm-network-mcp.qa`. With the 4a–4d steps above, `no_outfall_path` warnings should be limited to genuinely disconnected sub-graphs (e.g. trunk pipes that fall outside the basin clip).
5b. `swmm-network-mcp.assign_subcatchment_outlets` — REQUIRED when the subcatchments came out of `basin_shp_to_subcatchments` (which seeds every subcatchment's `outlet` to the literal outfall). This step rewrites each subcatchment's `outlet` to a real upstream junction (`mode=nearest_junction` reads `network.json`'s junctions list; or `mode=manual_lookup` for an explicit mapping). Without it the pipe network is in the .inp but receives no surface runoff. Pass the rewritten CSV (not the original) to `build_inp` in step 6.
6. `swmm-builder-mcp.build_inp`.
7. `swmm-runner-mcp.swmm_run`.
8. `swmm-runner-mcp.swmm_continuity`.
9. `swmm-runner-mcp.swmm_peak`.

Stop and report if a required MCP contract is absent. Continue with a temporary script only when the user explicitly asked to test the framework and the run manifest records the gap under `framework_gaps`.

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
10. optional `swmm-uncertainty`
11. optional `swmm-lid-optimization`
12. `swmm-experiment-audit`

Exact MCP call chain for the full modular path. Two parallel branches
(GIS / hydrology vs network) merge at `build_inp`. Region-agnostic:
substitute the user's basin shapefile, pipe shapefile, watercourse
shapefile, landuse layer, soil layer, and rainfall input wherever the
inputs are referenced.

**GIS / hydrology branch (subcatchments + params)**
1. `swmm-gis-mcp.basin_shp_to_subcatchments` — basin shp → subcatchments.geojson + .csv. (Or `gis_preprocess_subcatchments` if a pre-attributed subcatchment shp + DEM exist.)
2. `swmm-gis-mcp.qgis_area_weighted_params` — intersect subcatchments × landuse × soil → weighted_params.json. Inspect the `warnings` array for any landuse classes that fell through to DEFAULT.
3. (alternative when a richer params chain is wanted) `swmm-params-mcp.map_landuse` → `map_soil` → `merge_params`.

**Climate branch (rainfall)**
4. `swmm-climate-mcp.format_rainfall` — rainfall CSV (or `.dat` via `inputDatPaths`) → rainfall.json + timeseries.txt.
5. `swmm-climate-mcp.build_raingage_section` (only when an explicit raingage artefact is needed).

**Network branch (pipe topology)**
6. `swmm-network-mcp.prepare_storm_inputs` — clip pipes (+optional manholes) shp to the basin, fill mapping.json from `templates/city_mapping_raw_shapefile.template.json`. (Skip when the source is already a structured CAD export with explicit from/to nodes.)
7. `swmm-network-mcp.snap_pipe_endpoints` — heal sub-millimetre vertex drift between pipe segments so `import_city_network` can infer connected junctions. (`BACKLOG.md B8`; skip pre-B8.)
8. `swmm-network-mcp.infer_outfall` — pick a single outfall point (mode `endpoint_nearest_watercourse` when a watercourse shp is available, else `lowest_endpoint`).
9. `swmm-network-mcp.reorient_pipes` — BFS-from-outfall flow-direction fix.
10. `swmm-network-mcp.import_city_network` — assemble network.json from the prepared geojsons + mapping.
11. `swmm-network-mcp.qa` — topology + required-attribute QA.

**Wiring + assembly**
12. `swmm-network-mcp.assign_subcatchment_outlets` — REQUIRED if subcatchments came from `basin_shp_to_subcatchments` (which writes `outlet=OUT1` as a placeholder). Rewrites the CSV so each subcatchment drains into a real upstream junction; without this the pipe network sits idle.
13. `swmm-builder-mcp.build_inp` — assemble model.inp + builder manifest.

**Run + QA + plot**
14. `swmm-runner-mcp.swmm_run` — omit the `node` arg to auto-detect the first `[OUTFALLS]` entry from the .inp.
15. `swmm-runner-mcp.swmm_continuity`
16. `swmm-runner-mcp.swmm_peak` — pass the actual outfall name explicitly (no longer defaults to `O1`).
17. optional `swmm-plot-mcp.plot_rain_runoff_si` — paired rain + flow figure for any node.
14. optional calibration tools:
   - `swmm-calibration-mcp.swmm_sensitivity_scan`
   - `swmm-calibration-mcp.swmm_calibrate`
   - `swmm-calibration-mcp.swmm_calibrate_search`
   - `swmm-calibration-mcp.swmm_validate`
   - `swmm-calibration-mcp.swmm_parameter_scout`
15. optional uncertainty scripts:
   - `python3 skills/swmm-uncertainty/scripts/uncertainty_propagate.py ...`
   - `python3 skills/swmm-uncertainty/scripts/monte_carlo_propagate.py ...`
16. optional LID scripts:
   - `python3 skills/swmm-lid-optimization/scripts/entropy_lid_priority.py ...`
   - `python3 skills/swmm-lid-optimization/scripts/lid_scenario_builder.py ...`
17. `swmm-experiment-audit` via CLI:
   - `python3 skills/swmm-experiment-audit/scripts/audit_run.py --run-dir runs/<case>`
   - By default this also writes the audit note into `~/Documents/Agentic-SWMM-Obsidian-Vault/20_Audit_Layer/Experiment_Audits` and updates `Experiment Audit Index.md`.

### Mode B: Prepared-input build
Use this when `subcatchments.csv`, `network.json`, params JSON, and rainfall references already exist.

Execution order:
1. `swmm-builder`
2. `swmm-runner`
3. QA checks
4. optional plotting / calibration
5. optional uncertainty / LID scenario analysis
6. `swmm-experiment-audit`

Exact MCP call chain for prepared inputs:
1. `swmm-builder-mcp.build_inp`
2. `swmm-runner-mcp.swmm_run`
3. `swmm-runner-mcp.swmm_continuity`
4. `swmm-runner-mcp.swmm_peak`
5. optional `swmm-plot-mcp.plot_rain_runoff_si`
6. optional calibration tools
7. optional uncertainty / LID scripts where a runnable base INP exists
8. `swmm-experiment-audit` via CLI:
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
  - build + run + QA + calibration / uncertainty / LID scenarios?

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
- optional `runs/<case>/09_uncertainty/...`
- optional `runs/<case>/09_lid/...`
- `runs/<case>/09_audit/experiment_provenance.json`
- `runs/<case>/09_audit/comparison.json`
- `runs/<case>/09_audit/experiment_note.md`

## Preflight
Before running any operating mode, every MCP server under `mcp/<server>/`
must have its `node_modules` installed. `node_modules/` is `.gitignored`,
so a fresh clone (or any server added later) needs an install step:

```bash
scripts/install_mcp_deps.sh                  # install for all mcp/*/ servers
scripts/install_mcp_deps.sh swmm-calibration # install for one server
```

The script loops over every `mcp/*/package.json` and runs `npm install`.
Exit code is non-zero if any install fails. Servers that fail to install
will not respond to `tools/list`, so a cold-start agent that skips this
step will see ambiguous "MCP server exited before response" errors deep
into Mode 0 instead of a clean install failure up front.

After install, verify with a `tools/list` probe per server, for example:

```bash
python3 skills/swmm-end-to-end/scripts/mcp_stdio_call.py \
  --server-dir mcp/swmm-calibration --tool __probe__ \
  --arguments-json '{}' --out-response /tmp/_.json
```

The harness will print the `Available tools: [...]` for that server in its
error output (a dedicated `--list-tools` flag is on the framework backlog).

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
- The audit must write `09_audit/experiment_provenance.json`, `09_audit/comparison.json`, and Obsidian-compatible `09_audit/experiment_note.md` inside the run directory.
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
- `09_audit/experiment_provenance.json`
- `09_audit/comparison.json`
- Obsidian-compatible `09_audit/experiment_note.md`

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
