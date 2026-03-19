# agentic-swmm-workflow

**Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw**

Authors: **Zhonghao Zhang** & **Caterina Valeo**  
License: **MIT**

A reproducible, automation-friendly workflow for **EPA SWMM** that supports:

- **Automated run management** (standard run directory, inputs/outputs, `manifest.json` provenance)
- **Built-in verification checks** (continuity/mass balance, equivalence checks across interfaces)
- **Publication-grade plotting** (consistent styling for rainfall–runoff figures)
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
- **MCP layer:** tool interfaces (GIS / SWMM / Plot)
- **Engine layer:** SWMM engine (`swmm5`)
- **Output layer:** standardized run directory (`INP/RPT/OUT`, manifest, plots)
- **Verification layer:** checks for equivalence + continuity + preprocessing consistency

## What’s included

- `skills/swmm-gis/`
  - DEM-based pour point selection (`boundary_min_elev`, `boundary_max_accum`)
  - MCP server: `swmm-gis-mcp`

- `skills/swmm-runner/`
  - Reproducible `swmm5` wrapper
  - Extracts peak flow/time and SWMM continuity tables from `.rpt`
  - Writes `manifest.json` (includes input SHA256 + engine version)
  - MCP server: `swmm-runner-mcp`

- `skills/swmm-plot/`
  - Publication-style rainfall–runoff plots (SI; rain as mm/Δt; inverted rain axis; Arial 12; inward ticks; no title; optional day/window focus)
  - MCP server: `swmm-plot-mcp`

- `examples/todcreek/model_chicago5min.inp`
  - Minimal example SWMM input used for demonstration.

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

### 3) Find a DEM-based pour point (optional)
```bash
python3 skills/swmm-gis/scripts/find_pour_point.py \
  --dem path/to/dem.tif \
  --method boundary_min_elev \
  --out-geojson runs/pour_point.geojson \
  --out-png runs/pour_point_preview.png
```

## MCP servers (optional)

Each skill includes an MCP server you can run via stdio:

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

## Citation

### APA (repository)
Zhang, Z., & Valeo, C. (2026). *agentic-swmm-workflow* [Computer software]. GitHub. https://github.com/Zhonghao1995/agentic-swmm-workflow

### APA (manuscript, if needed)
Zhang, Z., & Valeo, C. (2026). Agentic modelling pipeline: Reproducible rapid stormwater modelling management system with OpenClaw [Preprint]. EarthArXiv. https://doi.org/10.31223/X5F47G
