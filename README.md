# agentic-swmm-workflow

**Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw**

Authors: **Zhonghao Zhang** & **Caterina Valeo**  
License: **MIT**

A reproducible, automation-friendly workflow for **EPA SWMM** that supports:

- **Automated run management** (standard run directory, inputs/outputs, `manifest.json` provenance)
- **Built-in verification checks** (continuity/mass balance, equivalence checks across interfaces)
- **Publication-grade plotting** (consistent styling for rainfall–runoff figures)
- **Calibration / validation scaffold** for observed-vs-simulated scoring, explicit candidate parameter sets, and parameter scouting
- **Deterministic preprocessing + assembly layers** for GIS, climate, parameter mapping, network import, and full INP build
- Optional **agentic orchestration** via **OpenClaw Skills** exposed as **MCP (Model Context Protocol) servers**

## Architecture (Orchestration + MCP + Verification)

<p align="center">
  <a href="docs/figs/openclaw_swmm_pipeline.pdf">
    <img src="docs/figs/openclaw_swmm_pipeline.png" alt="OpenClaw + SWMM agentic modelling pipeline with verification layer" style="background:#ffffff; padding:12px; border-radius:8px;" width="900" />
  </a>
</p>

*(Click the figure to open the PDF version.)*

**Layers (left → right):**
- **Orchestrator layer:** OpenClaw (optional; coordinates tools/steps)
- **Skills layer:** SOP-style Skills (how the agent should run each tool safely/reproducibly)
- **MCP layer:** tool interfaces (GIS / Climate / Params / Network / Builder / SWMM / Plot / Calibration)
- **Engine layer:** SWMM engine (`swmm5`)
- **Output layer:** standardized run directory (`INP/RPT/OUT`, manifest, plots, summaries)
- **Verification layer:** checks for equivalence + continuity + preprocessing consistency

## Recommended usage pattern (SKILL + MCP + OpenClaw)

Use this stack in a simple way:

1. **SKILL (implementation layer)**
   - Put real logic in Python scripts under `skills/*/scripts/`.
2. **MCP (tool interface layer)**
   - Expose stable callable tools from each skill (`scripts/mcp/server.js`).
3. **OpenClaw (orchestration layer)**
   - Let OpenClaw call MCP tools step-by-step and manage workflow/reporting.

Recommendation:
- For manual local testing: call Python scripts directly.
- For agentic workflows and automation: prefer **MCP tools + OpenClaw**.
- Keep helper internals script-only; only expose stable workflow entrypoints in MCP.

## Repository skeleton

```text
agentic-swmm-workflow/
├─ README.md
├─ docs/
│  ├─ figs/
│  └─ repo-map.md
├─ examples/
│  ├─ todcreek/
│  │  └─ model_chicago5min.inp
│  └─ calibration/
├─ skills/
│  ├─ swmm-gis/
│  │  ├─ SKILL.md
│  │  ├─ examples/
│  │  │  └─ subcatchments_demo.geojson
│  │  └─ scripts/
│  │     ├─ find_pour_point.py
│  │     ├─ preprocess_subcatchments.py
│  │     └─ mcp/server.js
│  ├─ swmm-climate/
│  │  ├─ SKILL.md
│  │  ├─ examples/
│  │  │  └─ rainfall_event.csv
│  │  └─ scripts/
│  │     ├─ format_rainfall.py
│  │     ├─ build_raingage_section.py
│  │     └─ mcp/server.js
│  ├─ swmm-params/
│  │  ├─ SKILL.md
│  │  ├─ references/
│  │  ├─ examples/
│  │  └─ scripts/
│  │     ├─ landuse_to_swmm_params.py
│  │     ├─ soil_to_greenampt.py
│  │     ├─ merge_swmm_params.py
│  │     └─ mcp/server.js
│  ├─ swmm-network/
│  │  ├─ SKILL.md
│  │  ├─ examples/
│  │  └─ scripts/
│  │     ├─ network_import.py
│  │     ├─ network_qa.py
│  │     ├─ network_to_inp.py
│  │     ├─ schema/network_model.schema.json
│  │     └─ mcp/server.js
│  ├─ swmm-builder/
│  │  ├─ SKILL.md
│  │  ├─ examples/
│  │  └─ scripts/
│  │     ├─ build_swmm_inp.py
│  │     └─ mcp/server.js
│  ├─ swmm-runner/
│  │  ├─ SKILL.md
│  │  └─ scripts/
│  │     ├─ swmm_runner.py
│  │     └─ mcp/server.js
│  ├─ swmm-plot/
│  │  ├─ SKILL.md
│  │  └─ scripts/
│  │     ├─ plot_rain_runoff_si.py
│  │     └─ mcp/server.js
│  └─ swmm-calibration/
│     ├─ SKILL.md
│     ├─ examples/
│     └─ scripts/
│        ├─ swmm_calibrate.py
│        ├─ parameter_scout.py
│        ├─ iterative_calibration.py
│        └─ mcp/server.js
└─ runs/ (generated artifacts)
```

## What’s included

For a larger local development map (extra experiments/runs/data), see `docs/repo-map.md`.

- `skills/swmm-gis/`
  - DEM pour point selection
  - subcatchment polygon preprocessing (area/width/slope/outlet linking)
  - MCP server: `swmm-gis-mcp`
- `skills/swmm-climate/`
  - rainfall CSV -> SWMM `[TIMESERIES]` formatting
  - `[RAINGAGES]` helper snippet builder
  - MCP server: `swmm-climate-mcp`
- `skills/swmm-params/`
  - landuse + soil deterministic mapping to SWMM hydrology parameters
  - merged params JSON for builder
  - MCP server: `swmm-params-mcp`
- `skills/swmm-network/`
  - network schema, importer, QA, and INP section export
  - MCP server: `swmm-network-mcp`
- `skills/swmm-builder/`
  - assembles full runnable INP from subcatchments + params + network + climate references
  - writes manifest with input hashes, validation results, and section diagnostics
  - MCP server: `swmm-builder-mcp`
- `skills/swmm-runner/`
  - reproducible `swmm5` execution + run manifest
  - continuity and peak extraction tools
  - MCP server: `swmm-runner-mcp`
- `skills/swmm-plot/`
  - rainfall-runoff figure generation
  - MCP server: `swmm-plot-mcp`
- `skills/swmm-calibration/`
  - calibration/validation/sensitivity scaffold
  - MCP server: `swmm-calibration-mcp`

## Verification (what this repo aims to guarantee)

This repository is designed so that automation is *auditable*:

- **SWMM CLI ↔ SWMM MCP equivalence:** MCP-run results should match direct `swmm5` runs (same INP, same engine, same outputs within expected tolerances)
- **SWMM GUI (manual) ↔ workflow equivalence (where applicable):** supports sanity-check comparisons when reproducing a GUI workflow
- **Continuity / mass balance verification:** continuity tables are parsed from `.rpt` and surfaced as diagnostics
- **Preprocessing consistency checks (GIS/DEM):** pour point methods are deterministic and outputs can be re-generated

## Requirements

### Core (no OpenClaw required)
- `swmm5` available on your `PATH` (EPA SWMM engine)
- Python 3.x

Recommended Python packages (vary by modules used):
- `swmmtoolbox` (reads `.out` for plotting/time-series comparisons)
- `matplotlib`, `numpy`
- `rasterio` (DEM I/O)
- `pysheds` (flow accumulation)
- `pandas` (observed-flow parsing and metric alignment)

### Optional (agentic / MCP)
- Node.js 18+ (each MCP server has its own `package.json`)
- OpenClaw (only if you want the orchestrated “agentic” interface)

## Quick start (CLI-only, no OpenClaw)

### 1) Run SWMM and write a manifest
```bash
python3 skills/swmm-runner/scripts/swmm_runner.py run \
  --inp examples/todcreek/model_chicago5min.inp \
  --run-dir runs/demo \
  --node O1
```

### 2) Plot rainfall–runoff (publication spec)
```bash
python3 skills/swmm-plot/scripts/plot_rain_runoff_si.py \
  --inp runs/demo/model.inp \
  --out runs/demo/model.out \
  --out-png runs/demo/fig_rain_runoff.png \
  --focus-day 1984-05-25 \
  --window-start 09:00 \
  --window-end 15:00 \
  --dt-min 5
```

### 3) Run an MVP calibration dry-run (explicit candidate sets)
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py calibrate \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --parameter-sets examples/calibration/parameter_sets.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/calibration \
  --swmm-node O1 \
  --objective nse \
  --summary-json runs/calibration/summary.json \
  --best-params-out runs/calibration/best_params.json \
  --dry-run
```

### 4) Run a parameter scout pass
```bash
python3 skills/swmm-calibration/scripts/parameter_scout.py \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --base-params examples/calibration/base_params.json \
  --scan-spec examples/calibration/scan_spec.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/parameter-scout \
  --summary-json runs/parameter-scout/summary.json \
  --swmm-node O1
```

### 5) Build first-pass subcatchment parameters (land use + soil)
```bash
python3 skills/swmm-params/scripts/landuse_to_swmm_params.py \
  --input skills/swmm-params/examples/landuse_input.csv \
  --output runs/swmm-params/example_landuse.json

python3 skills/swmm-params/scripts/soil_to_greenampt.py \
  --input skills/swmm-params/examples/soil_input.csv \
  --output runs/swmm-params/example_soil.json

python3 skills/swmm-params/scripts/merge_swmm_params.py \
  --landuse-json runs/swmm-params/example_landuse.json \
  --soil-json runs/swmm-params/example_soil.json \
  --output runs/swmm-params/example_builder_params.json
```

### 6) Format rainfall for SWMM `[TIMESERIES]` and `[RAINGAGES]`
```bash
python3 skills/swmm-climate/scripts/format_rainfall.py \
  --input skills/swmm-climate/examples/rainfall_event.csv \
  --out-json runs/swmm-climate/example_rainfall.json \
  --out-timeseries runs/swmm-climate/example_timeseries.txt \
  --series-name TS_EVENT

python3 skills/swmm-climate/scripts/build_raingage_section.py \
  --rainfall-json runs/swmm-climate/example_rainfall.json \
  --gage-id RG1 \
  --interval-min 5 \
  --out-text runs/swmm-climate/example_raingage.txt \
  --out-json runs/swmm-climate/example_raingage.json
```

### 7) Preprocess subcatchment polygons for builder input
```bash
python3 skills/swmm-gis/scripts/preprocess_subcatchments.py \
  --subcatchments-geojson skills/swmm-gis/examples/subcatchments_demo.geojson \
  --network-json skills/swmm-network/examples/basic-network.json \
  --default-rain-gage RG1 \
  --out-csv runs/swmm-gis/subcatchments_preprocessed.csv \
  --out-json runs/swmm-gis/subcatchments_preprocessed.json
```

### 8) Assemble a runnable INP with `swmm-builder`
```bash
python3 skills/swmm-builder/scripts/build_swmm_inp.py \
  --subcatchments-csv runs/swmm-gis/subcatchments_preprocessed.csv \
  --params-json runs/swmm-params/example_builder_params.json \
  --network-json skills/swmm-network/examples/basic-network.json \
  --rainfall-json runs/swmm-climate/example_rainfall.json \
  --raingage-json runs/swmm-climate/example_raingage.json \
  --config-json skills/swmm-builder/examples/options_config.json \
  --out-inp runs/swmm-builder/example_model.inp \
  --out-manifest runs/swmm-builder/example_manifest.json
```
Notes:
- `swmm-builder` now fails fast on missing/invalid critical fields for `[OPTIONS]`, `[RAINGAGES]`, `[TIMESERIES]`, `[SUBCATCHMENTS]`, `[SUBAREAS]`, `[INFILTRATION]`, and current network sections.
- Manifest includes `validation` and `validation_diagnostics` for audit/debug.

### 9) Import a pipe network (GeoJSON) and export SWMM sections
```bash
python3 skills/swmm-network/scripts/network_import.py \
  --conduits skills/swmm-network/examples/import-conduits.geojson \
  --junctions skills/swmm-network/examples/import-junctions.geojson \
  --outfalls skills/swmm-network/examples/import-outfalls.geojson \
  --mapping skills/swmm-network/examples/import-mapping.json \
  --out runs/swmm-network/imported-network.json

python3 skills/swmm-network/scripts/network_qa.py \
  runs/swmm-network/imported-network.json

python3 skills/swmm-network/scripts/network_to_inp.py \
  runs/swmm-network/imported-network.json \
  --out runs/swmm-network/imported-network.inp
```

### 10) Step-1 acceptance run (end-to-end publish pipeline)
```bash
python3 scripts/acceptance/run_acceptance.py --run-id latest
```

This command executes:
- sample inputs -> `swmm-gis` preprocess
- `swmm-params` mapping + merge
- `swmm-climate` formatting + raingage build
- `swmm-builder` INP assembly
- `swmm-runner` execution
- QA checks (network QA + runner continuity/peak parse)

Artifacts are written under `runs/acceptance/<run-id>/`, including:
- built INP
- runner `.rpt` and `.out`
- `manifest.json`
- `acceptance_report.json` and `acceptance_report.md`

## MCP servers (optional)

Each skill includes an MCP server you can run via stdio:

- Builder MCP:
```bash
cd skills/swmm-builder/scripts/mcp && npm install && npm start
```

- Climate MCP:
```bash
cd skills/swmm-climate/scripts/mcp && npm install && npm start
```

- SWMM runner MCP:
```bash
cd skills/swmm-runner/scripts/mcp && npm install && npm start
```

- Plot MCP:
```bash
cd skills/swmm-plot/scripts/mcp && npm install && npm start
```

- GIS MCP:
```bash
cd skills/swmm-gis/scripts/mcp && npm install && npm start
```

- Calibration MCP:
```bash
cd skills/swmm-calibration/scripts/mcp && npm install && npm start
```

- Network MCP:
```bash
cd skills/swmm-network/scripts/mcp && npm install && npm start
```

- Params MCP:
```bash
cd skills/swmm-params/scripts/mcp && npm install && npm start
```

`swmm-builder-mcp` exposes:
- `build_inp`

`swmm-climate-mcp` exposes:
- `format_rainfall`
- `build_raingage_section`

`swmm-gis-mcp` exposes:
- `gis_find_pour_point`
- `gis_preprocess_subcatchments`

`swmm-calibration-mcp` exposes:
- `swmm_parameter_scout`
- `swmm_sensitivity_scan`
- `swmm_calibrate`
- `swmm_validate`

`swmm-network-mcp` exposes:
- `import_network`
- `qa`
- `export_inp`
- `summary`

`swmm-params-mcp` exposes:
- `map_landuse`
- `map_soil`
- `merge_params`

## Calibration / validation scaffold (MVP)

`skills/swmm-calibration/` provides an auditable MVP scaffold:
- observed-flow parsing (`csv`, `tsv`, whitespace-delimited `dat`)
- candidate-set evaluation against a base `.inp`
- NSE/RMSE/bias/peak error metrics
- one-parameter scout and iterative runner
- JSON summaries for sensitivity, calibration, and validation

Current limits are intentional: no automatic global optimizer and no complex structural INP edits yet.

## Citation

### APA (repository)
Zhang, Z., & Valeo, C. (2026). *agentic-swmm-workflow* [Computer software]. GitHub. https://github.com/Zhonghao1995/agentic-swmm-workflow

### APA (manuscript / preprint)
Zhang, Z., & Valeo, C. (2026). *Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw*. https://doi.org/10.31223/X5F47G
