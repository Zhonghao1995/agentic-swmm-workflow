# agentic-swmm-workflow

**Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw**

Authors: **Zhonghao Zhang** & **Caterina Valeo**  
License: **MIT**

## What this project is

`agentic-swmm-workflow` is a **reproducible, agentic SWMM workflow** for stormwater modeling.
It is designed not just to let an LLM call SWMM, but to make **model assembly, execution, checking, and reporting auditable and repeatable**.

This framework can use either **OpenClaw** or **Hermes** as the orchestration layer, while still supporting direct **CLI-first deterministic execution**.

## Why this project is different

Many AI + SWMM demos focus on natural-language interaction with a model.
This repository focuses on a different problem: **how to make agentic hydrologic modeling reproducible, inspectable, and safe enough for research workflows**.

Key differentiators include:
- **End-to-end workflow framing:** GIS, climate, parameters, network assembly, model build, run, plotting, and calibration are organized as one coherent pipeline.
- **Manifest-driven provenance:** build/run/calibration stages emit standardized artifacts and `manifest.json` records for traceable reruns.
- **Verification by design:** continuity/mass-balance diagnostics, preprocessing consistency checks, and extracted peak checks are built into execution.
- **Deterministic CLI equivalence:** the same workflow can be run without an orchestrator for script-first, reproducible execution.
- **Research-oriented outputs:** standardized folders, parsed diagnostics, and publication-style plots support auditability and downstream reporting.

## Current capabilities

Implemented agentic capabilities already include:
- **Skills** for GIS, climate, parameters, network assembly, model build, run, plotting, and calibration.
- **MCP servers** for each module, enabling tool-level orchestration in OpenClaw.
- **Manifest-driven provenance** (`manifest.json`) across build, run, and calibration stages.
- **Verification checks** for continuity/mass balance, preprocessing consistency, and extracted peak metrics.
- **Calibration loop support** with explicit candidate sets and bounded search (`random`, `lhs`, `adaptive`).
- **Direct CLI execution** for deterministic, script-first runs without requiring an orchestrator.

## Where teams usually get stuck

- **Problem:** Workflows are fragmented across GIS, climate, parameter, and engine tools.  
  **Our response:** module-level Skills and MCP interfaces define clear handoffs and reduce manual transfer errors.
- **Problem:** Provenance is weak, so reruns and audits are hard.  
  **Our response:** build/run/calibration stages emit standardized artifacts and `manifest.json` records for traceable reruns.
- **Problem:** Model issues are often discovered late (continuity imbalance, interface mismatch).  
  **Our response:** verification checks run with execution and surface continuity and consistency diagnostics early.
- **Problem:** Calibration is often ad hoc and hard to reproduce.  
  **Our response:** calibration is encoded as explicit candidate sets and bounded search loops with reproducible outputs.
- **Problem:** Plotting and reporting quality varies across runs.  
  **Our response:** fixed plotting scripts generate consistent rainfall-runoff figures from SWMM outputs.

## Architecture (Orchestration + Skills + MCP + Verification)

<p align="center">
  <a href="docs/figs/openclaw_swmm_pipeline.pdf">
    <img src="docs/figs/openclaw_swmm_pipeline.png" alt="OpenClaw + SWMM agentic modelling pipeline with verification layer" style="background:#ffffff; padding:12px; border-radius:8px;" width="900" />
  </a>
</p>

*(Click the figure to open the PDF version.)*

**Layers (left → right):**
- **Orchestrator layer:** OpenClaw (optional; coordinates tools/steps)
- **Skills layer:** SOP-style Skills (how the agent should run each tool safely and reproducibly)
- **MCP layer:** tool interfaces (GIS / Climate / Params / Network / Builder / SWMM / Plot / Calibration)
- **Engine layer:** SWMM engine (`swmm5`)
- **Output layer:** standardized run directory (`INP/RPT/OUT`, manifest, plots, summaries)
- **Verification layer:** checks for equivalence, continuity, and preprocessing consistency

## End-to-end flow

1. Prepare deterministic inputs (GIS polygons, rainfall series, mapped soil/landuse parameters, and network schema/import).
2. Assemble a runnable SWMM `.inp` with `swmm-builder` and emit a build manifest.
3. Execute SWMM with `swmm-runner` and emit run-level manifest plus parsed diagnostics.
4. Verify continuity and extracted peak behavior.
5. Produce publication-style rainfall-runoff plots.
6. Optionally calibrate/validate with explicit sets or bounded search.

## Quickstart (CLI-only, no OpenClaw)

### Install (one line)

```bash
curl -fsSL https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/install.sh | bash
```

### 1) Install local dependencies

```bash
bash scripts/install.sh --yes
source .venv/bin/activate
```

### 2) Run the acceptance pipeline

```bash
python3 scripts/acceptance/run_acceptance.py --run-id latest
```

### 3) Check the acceptance report

Open this file after the run finishes:

`runs/acceptance/latest/acceptance_report.md`

### 4) Make a rainfall-runoff plot from acceptance outputs

```bash
mkdir -p runs/acceptance/latest/07_plot
python3 skills/swmm-plot/scripts/plot_rain_runoff_si.py \
  --inp runs/acceptance/latest/04_builder/model.inp \
  --out runs/acceptance/latest/05_runner/acceptance.out \
  --out-png runs/acceptance/latest/07_plot/fig_rain_runoff.png
```

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

## Collaboration

We welcome co-developers and research collaborators working on agentic hydrologic and stormwater modeling with OpenClaw and SWMM.

Contact:
- zhonghaoz@uvic.ca
- valeo@uvic.ca

## Citation

### APA (repository)
Zhang, Z., & Valeo, C. (2026). *agentic-swmm-workflow* [Computer software]. GitHub. https://github.com/Zhonghao1995/agentic-swmm-workflow

### APA (manuscript / preprint)
Zhang, Z., & Valeo, C. (2026). *Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw*. https://doi.org/10.31223/X5F47G
