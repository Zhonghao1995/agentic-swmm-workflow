# agentic-swmm-workflow

**Agentic SWMM Workflow (SWMM MCP + SWMM Skills)**

Authors: **Zhonghao Zhang** & **Caterina Valeo**  
License: **MIT**

This repository provides a reproducible, agentic workflow for **EPA SWMM** that separates:

1) **GIS/Preprocess** (e.g., pour point selection from DEM)
2) **SWMM execution** (run `swmm5`, extract peak flow + continuity diagnostics, write `manifest.json`)
3) **Publication-grade plotting** (SI units, inverted hyetograph axis, fixed styling)

The workflow is implemented as **OpenClaw Skills** and exposed as **MCP (Model Context Protocol) servers**.

## What’s included

- `skills/swmm-gis/`
  - DEM-based pour point selection (`boundary_min_elev`, `boundary_max_accum`)
  - MCP server: `swmm-gis-mcp`

- `skills/swmm-runner/`
  - Reproducible `swmm5` wrapper
  - Extracts peak flow/time and SWMM continuity tables from `.rpt`
  - Writes `manifest.json` (with input SHA256 + engine version)
  - MCP server: `swmm-runner-mcp`

- `skills/swmm-plot/`
  - Publication-style rainfall–runoff plots (SI; rain as mm/Δt; inverted rain axis; Arial 12; inward ticks; no title; optional day/window focus)
  - MCP server: `swmm-plot-mcp`

- `examples/todcreek/model_chicago5min.inp`
  - A minimal example SWMM input used for demonstration.

## Requirements

### SWMM
- `swmm5` available on your `PATH` (EPA SWMM engine).

### Python
Recommended:
- Python 3
- `swmmtoolbox` (reads `.out` for plotting/time-series comparisons)
- `matplotlib`, `numpy`
- `rasterio` (for DEM I/O)
- `pysheds` (for flow accumulation method)

### Node.js
- Node 18+
- Each MCP server directory contains its own `package.json` and dependencies.

## Quick start (CLI)

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

## MCP servers

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

## Notes on reproducibility

- The runner writes `manifest.json` per run, including `inp_sha256` and SWMM version (when detectable).
- Continuity errors are read from SWMM’s own `.rpt` continuity tables.

---

If you use this repository in academic work, please cite the corresponding manuscript by Zhang & Valeo.
