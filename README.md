# agentic-swmm-workflow

**Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw**

Authors: **Zhonghao Zhang** & **Caterina Valeo**  
License: **MIT**

## What this project does

Stormwater modeling is often fragmented: preprocessing is manual, runs are hard to audit, and figures are hard to reproduce consistently.

This project provides a deterministic, script-first workflow around **EPA SWMM** that takes you from GIS/climate/parameter inputs to a runnable model, verified outputs, and publication-ready plots.

The core idea is reproducibility: each run and build stage emits machine-readable artifacts (including `manifest.json`) so results can be traced, checked, and repeated.

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

## Capabilities

- **Automated run management + provenance:** standardized run folders and `manifest.json` outputs from build/run stages.
- **Verification checks:** continuity/mass-balance diagnostics, parsed peak metrics, and interface-equivalence support.
- **Publication-grade plotting:** consistent rainfall-runoff figure generation from SWMM outputs.
- **Calibration scaffold:** explicit candidate-set calibration, bounded search (`random`, `lhs`, `adaptive`), and one-parameter scout tools.
- **Deterministic preprocessing + assembly:** GIS, climate formatting, parameter mapping, network import/QA/export, and full INP build.
- **Optional orchestration:** direct CLI use or OpenClaw + MCP servers for agentic workflow coordination.

## End-to-end flow

1. Prepare deterministic inputs (GIS polygons, rainfall series, mapped soil/landuse parameters, and network schema/import).
2. Assemble a runnable SWMM `.inp` with `swmm-builder` and emit a build manifest.
3. Execute SWMM with `swmm-runner` and emit run-level manifest + parsed diagnostics.
4. Verify continuity and extracted peak behavior.
5. Produce publication-style rainfall-runoff plots.
6. Optionally calibrate/validate with explicit sets or bounded search.

## Minimal quickstart (CLI-only, no OpenClaw)

### Requirements

- `swmm5` available on your `PATH`
- Python 3.x
- Recommended Python packages (module-dependent): `swmmtoolbox`, `matplotlib`, `numpy`, `pandas`, `rasterio`, `pysheds`

### 1) Build a model deterministically (GIS + climate + params + builder)

```bash
RUN_ROOT=runs/quickstart
mkdir -p "$RUN_ROOT"/{01_gis,02_params,03_climate,04_builder}

python3 skills/swmm-gis/scripts/preprocess_subcatchments.py \
  --subcatchments-geojson skills/swmm-gis/examples/subcatchments_demo.geojson \
  --network-json skills/swmm-network/examples/basic-network.json \
  --default-rain-gage RG1 \
  --out-csv "$RUN_ROOT/01_gis/subcatchments_preprocessed.csv" \
  --out-json "$RUN_ROOT/01_gis/subcatchments_preprocessed.json"

python3 skills/swmm-params/scripts/landuse_to_swmm_params.py \
  --input skills/swmm-params/examples/landuse_input.csv \
  --output "$RUN_ROOT/02_params/landuse.json"

python3 skills/swmm-params/scripts/soil_to_greenampt.py \
  --input skills/swmm-params/examples/soil_input.csv \
  --output "$RUN_ROOT/02_params/soil.json"

python3 skills/swmm-params/scripts/merge_swmm_params.py \
  --landuse-json "$RUN_ROOT/02_params/landuse.json" \
  --soil-json "$RUN_ROOT/02_params/soil.json" \
  --output "$RUN_ROOT/02_params/params.json"

python3 skills/swmm-climate/scripts/format_rainfall.py \
  --input skills/swmm-climate/examples/rainfall_event.csv \
  --out-json "$RUN_ROOT/03_climate/rainfall.json" \
  --out-timeseries "$RUN_ROOT/03_climate/timeseries.txt" \
  --series-name TS_EVENT

python3 skills/swmm-climate/scripts/build_raingage_section.py \
  --rainfall-json "$RUN_ROOT/03_climate/rainfall.json" \
  --gage-id RG1 \
  --interval-min 5 \
  --out-text "$RUN_ROOT/03_climate/raingage.txt" \
  --out-json "$RUN_ROOT/03_climate/raingage.json"

python3 skills/swmm-builder/scripts/build_swmm_inp.py \
  --subcatchments-csv "$RUN_ROOT/01_gis/subcatchments_preprocessed.csv" \
  --params-json "$RUN_ROOT/02_params/params.json" \
  --network-json skills/swmm-network/examples/basic-network.json \
  --rainfall-json "$RUN_ROOT/03_climate/rainfall.json" \
  --raingage-json "$RUN_ROOT/03_climate/raingage.json" \
  --config-json skills/swmm-builder/examples/options_config.json \
  --out-inp "$RUN_ROOT/04_builder/model.inp" \
  --out-manifest "$RUN_ROOT/04_builder/manifest.json"
```

### 2) Run SWMM with run-level manifest/provenance

```bash
python3 skills/swmm-runner/scripts/swmm_runner.py run \
  --inp runs/quickstart/04_builder/model.inp \
  --run-dir runs/quickstart/05_runner \
  --node O1
```

### 3) Verify continuity + peak diagnostics

```bash
python3 skills/swmm-runner/scripts/swmm_runner.py continuity \
  --rpt runs/quickstart/05_runner/model.rpt

python3 skills/swmm-runner/scripts/swmm_runner.py peak \
  --rpt runs/quickstart/05_runner/model.rpt \
  --node O1
```

### 4) Produce a publication-style rainfall-runoff figure

```bash
mkdir -p runs/quickstart/06_plot
python3 skills/swmm-plot/scripts/plot_rain_runoff_si.py \
  --inp runs/quickstart/05_runner/model.inp \
  --out runs/quickstart/05_runner/model.out \
  --out-png runs/quickstart/06_plot/fig_rain_runoff.png \
  --focus-day 1984-05-25 \
  --window-start 09:00 \
  --window-end 15:00 \
  --dt-min 5
```

### 5) Optional: run bounded calibration search (LHS)

```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py search \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --search-space examples/calibration/search_space.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/calibration-search \
  --summary-json runs/calibration-search/summary.json \
  --ranking-json runs/calibration-search/ranking.json \
  --strategy lhs \
  --iterations 12 \
  --seed 42 \
  --dry-run
```

For explicit candidate sets, use `swmm_calibrate.py calibrate` with `examples/calibration/parameter_sets.json`. For one-parameter scouting, use `skills/swmm-calibration/scripts/parameter_scout.py`.

## Advanced modules and docs

- Each module has a focused skill doc (`skills/<module>/SKILL.md`) with implementation details and extra examples.
- For a larger local repo map (including runs/data), see `docs/repo-map.md`.
- For one-command end-to-end acceptance execution, use `scripts/acceptance/run_acceptance.py --run-id latest`.
- For orchestration, each module exposes an MCP server at `skills/<module>/scripts/mcp/server.js` (optional OpenClaw integration).

## Repository skeleton

```text
agentic-swmm-workflow/
├─ README.md
├─ docs/
│  ├─ figs/openclaw_swmm_pipeline.{png,pdf}
│  └─ repo-map.md
├─ examples/
│  ├─ todcreek/model_chicago5min.inp
│  └─ calibration/
├─ scripts/
│  └─ acceptance/run_acceptance.py
├─ skills/
│  ├─ swmm-gis/
│  ├─ swmm-climate/
│  ├─ swmm-params/
│  ├─ swmm-network/
│  ├─ swmm-builder/
│  ├─ swmm-runner/
│  ├─ swmm-plot/
│  └─ swmm-calibration/
└─ runs/ (generated artifacts)
```

## Citation

### APA (repository)
Zhang, Z., & Valeo, C. (2026). *agentic-swmm-workflow* [Computer software]. GitHub. https://github.com/Zhonghao1995/agentic-swmm-workflow

### APA (manuscript / preprint)
Zhang, Z., & Valeo, C. (2026). *Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw*. https://doi.org/10.31223/X5F47G
