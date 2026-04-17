# agentic-swmm-workflow

**Agentic SWMM for reproducible stormwater modeling**  
*OpenClaw or Hermes + MCP + SWMM + verification-first workflow*

Authors: **Zhonghao Zhang** & **Caterina Valeo**  
License: **MIT**

`agentic-swmm-workflow` is an open-source framework for building **reproducible, agentic SWMM workflows** with **OpenClaw** or **Hermes** as the recommended orchestration layer.
It helps researchers and developers move from scattered scripts and manual clicks to a pipeline that is **structured, auditable, and easier to rerun**.

At the core of this project is a simple idea: **use OpenClaw or Hermes to operate a modular SWMM workflow through Skills and MCP tools, while keeping the underlying modeling steps deterministic and inspectable**.

Unlike a simple chat-to-SWMM wrapper, this repository focuses on the full workflow: **prepare inputs, assemble models, run SWMM, verify outputs, plot results, and calibrate with traceable artifacts**.

## Why people star this project

- **OpenClaw and Hermes ready:** the workflow is designed to be operated through modern agentic runtimes rather than only through ad hoc scripts.
- **End-to-end workflow:** GIS, climate, parameters, network assembly, model build, run, plotting, and calibration in one repo.
- **Reproducible by default:** build/run/calibration stages emit standardized artifacts and `manifest.json` provenance.
- **Verification-first:** continuity, mass-balance, preprocessing consistency, and parsed peak checks are built into execution.
- **Works with or without an agent:** run directly from CLI, or orchestrate with **OpenClaw** or **Hermes**.
- **Made for research and tooling:** clean outputs, deterministic runs, publication-style plots, and modular skill-level interfaces.

## Who this is for

This project is useful if you are:
- a **SWMM user** who wants cleaner and more repeatable workflows,
- a **hydrology or stormwater researcher** exploring reproducible computational pipelines,
- a **developer building AI tools for environmental modeling**, or
- a **collaborator** interested in MCP tools, orchestration, calibration, or hydrologic QA.

## What you can do with it

Implemented capabilities already include:
- **Skills** for GIS, climate, parameters, network assembly, model build, run, plotting, and calibration.
- A top-level **`swmm-end-to-end` orchestration skill** for OpenClaw-facing build/run/QA flows.
- **MCP servers** for each module, enabling tool-level orchestration in OpenClaw.
- **Manifest-driven provenance** (`manifest.json`) across build, run, and calibration stages.
- **Verification checks** for continuity/mass balance, preprocessing consistency, and extracted peak metrics.
- **Calibration loop support** with explicit candidate sets and bounded search (`random`, `lhs`, `adaptive`).
- **Direct CLI execution** for deterministic, script-first runs without requiring an orchestrator.

## Architecture

<p align="center">
  <a href="docs/figs/openclaw_swmm_pipeline.pdf">
    <img src="docs/figs/openclaw_swmm_pipeline.png" alt="OpenClaw + SWMM agentic modelling pipeline with verification layer" style="background:#ffffff; padding:12px; border-radius:8px;" width="900" />
  </a>
</p>

*(Click the figure to open the PDF version.)*

**Layers (left → right):**
- **Orchestrator layer:** OpenClaw (optional; coordinates tools and steps)
- **Skills layer:** SOP-style Skills describing how each tool should be run safely and reproducibly
- **MCP layer:** tool interfaces (GIS / Climate / Params / Network / Builder / SWMM / Plot / Calibration)
- **Engine layer:** SWMM engine (`swmm5`)
- **Output layer:** standardized run directory (`INP/RPT/OUT`, manifest, plots, summaries)
- **Verification layer:** checks for equivalence, continuity, and preprocessing consistency

## Quickstart

### Install (one line)

```bash
curl -fsSL https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/install.sh | bash
```

### Install local dependencies

```bash
bash scripts/install.sh --yes
source .venv/bin/activate
```

### Run the acceptance pipeline

```bash
python3 scripts/acceptance/run_acceptance.py --run-id latest
```

### Check the acceptance report

Open:

`runs/acceptance/latest/acceptance_report.md`

### Make a rainfall-runoff plot from acceptance outputs

```bash
mkdir -p runs/acceptance/latest/07_plot
python3 skills/swmm-plot/scripts/plot_rain_runoff_si.py \
  --inp runs/acceptance/latest/04_builder/model.inp \
  --out runs/acceptance/latest/05_runner/acceptance.out \
  --out-png runs/acceptance/latest/07_plot/fig_rain_runoff.png
```

## End-to-end flow

1. Prepare deterministic inputs (GIS polygons, rainfall series, mapped soil/landuse parameters, and network schema/import).
2. Assemble a runnable SWMM `.inp` with `swmm-builder` and emit a build manifest.
3. Execute SWMM with `swmm-runner` and emit run-level manifest plus parsed diagnostics.
4. Verify continuity and extracted peak behavior.
5. Produce publication-style rainfall-runoff plots.
6. Optionally calibrate and validate with explicit sets or bounded search.

## Repository map

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
│  ├─ acceptance/run_acceptance.py
│  └─ real_cases/run_todcreek_minimal.py
├─ skills/
│  ├─ swmm-gis/
│  ├─ swmm-climate/
│  ├─ swmm-params/
│  ├─ swmm-network/
│  ├─ swmm-builder/
│  ├─ swmm-runner/
│  ├─ swmm-plot/
│  ├─ swmm-calibration/
│  └─ swmm-end-to-end/
└─ runs/ (generated artifacts)
```

For more detail:
- See `docs/repo-map.md` for a broader repo walkthrough.
- See `skills/<module>/SKILL.md` for module-specific behavior and examples.
- Each module can expose an MCP server at `skills/<module>/scripts/mcp/server.js` for optional OpenClaw integration.

## OpenClaw orchestration

If you want one OpenClaw-facing entrypoint instead of manually choosing module skills, start with:

`skills/swmm-end-to-end/SKILL.md`

That top-level skill defines:
- when to use the full modular path,
- when to fall back to the Tod Creek minimal real-data runner,
- which QA gates must pass before a run is considered usable, and
- when to stop instead of inventing missing network/subcatchment inputs.

For the exact MCP tool-call sequence behind that skill, see:

`docs/openclaw-execution-path.md`

## Where collaborators can help

Contributions are especially welcome in:
- additional SWMM case studies and benchmark datasets,
- stronger calibration and validation workflows,
- new MCP tools and orchestration patterns,
- QA and regression testing,
- documentation, tutorials, and onboarding examples,
- interoperability with GIS, ML, and hydrologic toolchains.

If you are interested in collaborating, opening an issue or reaching out by email is welcome.

Contact:
- zhonghaoz@uvic.ca
- valeo@uvic.ca

## Roadmap

Planned or actively explored directions include:
- improved benchmark and acceptance cases,
- richer calibration agents and search strategies,
- stronger report generation and experiment summaries,
- broader GIS and data-ingestion tooling,
- cleaner onboarding for external contributors,
- deeper reproducibility and equivalence testing.

## Citation

### APA (repository)
Zhang, Z., & Valeo, C. (2026). *agentic-swmm-workflow* [Computer software]. GitHub. https://github.com/Zhonghao1995/agentic-swmm-workflow

### APA (manuscript / preprint)
Zhang, Z., & Valeo, C. (2026). *Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw*. https://doi.org/10.31223/X5F47G
