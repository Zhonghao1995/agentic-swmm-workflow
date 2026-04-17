---
name: swmm-end-to-end
description: Top-level orchestration skill for OpenClaw-driven SWMM modelling. Use when Zhonghao wants one agent-facing skill that decides which module tools to run, in what order, and when to stop, for example to build, run, QA, and optionally calibrate a SWMM case from prepared or partially prepared inputs.
---

# SWMM End-to-End Orchestration

## What this skill provides
- A top-level orchestration contract for OpenClaw.
- A deterministic execution order across the existing module skills:
  - `swmm-gis`
  - `swmm-climate`
  - `swmm-params`
  - `swmm-network`
  - `swmm-builder`
  - `swmm-runner`
  - `swmm-plot`
  - `swmm-calibration`
- Clear stop conditions so the agent does not pretend a full model was built when critical inputs are still missing.
- A minimal real-data fallback path for Tod Creek via `scripts/real_cases/run_todcreek_minimal.py`.

## When to use this skill
Use this skill when the user asks for:
- one OpenClaw-facing entrypoint for SWMM modelling,
- end-to-end build + run + QA,
- an agent to decide which SWMM module comes next,
- a real-data dry run before full automation is ready, or
- a bounded orchestration layer without rewriting the underlying scripts.

Do **not** use this skill when the user clearly wants only one module in isolation, such as only rainfall formatting or only calibration metrics.

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

### Mode B: Prepared-input build
Use this when `subcatchments.csv`, `network.json`, params JSON, and rainfall references already exist.

Execution order:
1. `swmm-builder`
2. `swmm-runner`
3. QA checks
4. optional plotting / calibration

### Mode C: Minimal real-data Tod Creek fallback
Use this only when the user wants a real-data run but the full modular path is not ready because there is no trustworthy multi-subcatchment + network input yet.

Script:
- `scripts/real_cases/run_todcreek_minimal.py`

Characteristics:
- one subcatchment
- simple junction/outfall/conduit layout
- real DEM / land use / soil / rainfall inputs
- useful as a real-data smoke test, not as the final watershed architecture

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
