# agentic-swmm-workflow

**Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw**

Authors: **Zhonghao Zhang** & **Caterina Valeo**  
License: **MIT**

A reproducible, automation-friendly workflow for **EPA SWMM** that supports:

- **Automated run management** (standard run directory, inputs/outputs, `manifest.json` provenance)
- **Built-in verification checks** (continuity/mass balance, equivalence checks across interfaces)
- **Publication-grade plotting** (consistent styling for rainfall–runoff figures)
- **Calibration / validation scaffold** for observed-vs-simulated scoring, explicit candidate parameter sets, and parameter scouting
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
- **MCP layer:** tool interfaces (GIS / SWMM / Plot / Calibration)
- **Engine layer:** SWMM engine (`swmm5`)
- **Output layer:** standardized run directory (`INP/RPT/OUT`, manifest, plots, summaries)
- **Verification layer:** checks for equivalence + continuity + preprocessing consistency

## What’s included

If you are looking for the **larger local development workspace** (with many more files, experiments, runs, and Tod Creek data), see `docs/repo-map.md`.

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

- `skills/swmm-calibration/`
  - Calibration / validation / sensitivity-analysis scaffold
  - Parameter scout for ranking which parameters matter first and which direction to move them
  - Reads observed flow from delimited text files, patches selected INP values, runs SWMM, and scores candidate parameter sets
  - MCP server: `swmm-calibration-mcp`
  - Current scope is intentionally MVP: explicit candidate parameter sets, one-parameter-at-a-time scout, simple line-oriented INP patching, and transparent limitations

- `examples/todcreek/model_chicago5min.inp`
  - Minimal example SWMM input used for demonstration.

- `examples/calibration/`
  - Minimal example files for calibration / validation / parameter scout wiring
  - See `examples/calibration/README.md`

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

- Calibration MCP:
```bash
cd skills/swmm-calibration/scripts/mcp && npm install && npm start
```

`swmm-calibration-mcp` exposes:
- `swmm_parameter_scout`
- `swmm_sensitivity_scan`
- `swmm_calibrate`
- `swmm_validate`

## Calibration / validation scaffold (MVP)

The repository now includes a first-pass calibration scaffold under `skills/swmm-calibration/`.

What it does today:
- reads observed flow from common delimited text formats (`csv`, `tsv`, whitespace-delimited `dat`)
- evaluates explicit candidate parameter sets against a base `.inp`
- computes NSE, RMSE, bias, peak-flow error, and peak-timing error
- runs a minimal one-parameter-at-a-time scout to identify promising parameters and narrower next ranges
- writes trial folders plus JSON summaries for parameter scouting, sensitivity, calibration, and validation runs
- includes a minimal example config in `examples/calibration/`

What it does **not** pretend to do yet:
- automatic global optimization out of the box
- arbitrary INP structural edits
- full interaction-aware parameter search
- robust support for every historical field logger format without light cleanup

This is deliberate: the scaffold is meant to be auditable and easy to extend into a fuller calibration layer.

## Citation

### APA (repository)
Zhang, Z., & Valeo, C. (2026). *agentic-swmm-workflow* [Computer software]. GitHub. https://github.com/Zhonghao1995/agentic-swmm-workflow

### APA (manuscript, if needed)
Zhang, Z., & Valeo, C. (2026). *Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw* [Manuscript in preparation].
