# Agentic SWMM Workflow

<p align="center">
  <img src="docs/figs/agentic_swmm_logo.png" alt="Agentic SWMM logo with agentic robot, stormwater system, and SWMM wordmark" width="900" />
</p>

<p align="center">
  <a href="https://github.com/Zhonghao1995/agentic-swmm-workflow/actions/workflows/ci.yml">
    <img src="https://github.com/Zhonghao1995/agentic-swmm-workflow/actions/workflows/ci.yml/badge.svg" alt="CI status" />
  </a>
  <a href="https://github.com/Zhonghao1995/agentic-swmm-workflow/releases/latest">
    <img src="https://img.shields.io/github/v/release/Zhonghao1995/agentic-swmm-workflow?label=release&color=1F6FEB" alt="latest release" />
  </a>
  <a href="#try-it-in-one-command">
    <img src="https://img.shields.io/badge/install-one--command-0B74DE" alt="one-command install" />
  </a>
  <a href="#try-it-in-one-command">
    <img src="https://img.shields.io/badge/docker-v0.3.0%20reproducible-2496ED" alt="Docker reproducible environment" />
  </a>
  <a href="https://github.com/USEPA/Stormwater-Management-Model">
    <img src="https://img.shields.io/badge/SWMM-5.2-blue" alt="EPA SWMM 5.2" />
  </a>
  <a href="#openclaw--hermes-orchestration">
    <img src="https://img.shields.io/badge/OpenClaw%20%2F%20Hermes-ready-1F6FEB" alt="OpenClaw or Hermes ready" />
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license" />
  </a>
</p>

**Agentic SWMM for reproducible stormwater modeling**  
*[OpenClaw](https://github.com/openclaw/openclaw) or [Hermes Agent](https://github.com/NousResearch/hermes-agent) + Skills + MCP + SWMM + verification-first workflow + Obsidian-compatible audit*

**A five-minute, one-command, auditable EPA SWMM workflow for agentic environmental modelling.**

`agentic-swmm-workflow` helps researchers and developers install SWMM, run reproducible benchmark workflows, verify outputs, audit evidence, organize experiment memory in Obsidian, and extend SWMM modelling through modular Skills and MCP-ready tools.

This project is not a loose collection of Python scripts or a simple chat-to-SWMM wrapper. It is an agent-ready modelling workflow: the agent coordinates the stages, while SWMM execution, generated files, QA checks, provenance, plots, experiment notes, and memory handoff remain reproducible, inspectable, and supported by explicit artifacts.

Authors: **Zhonghao Zhang** & **Caterina Valeo**  
License: **MIT**

## Try it in one command

For reproducible `v0.3.0` results, Docker is the simplest path. It runs the fixed container image and writes artifacts to `./agentic-swmm-runs/`.

### Docker reproducible run

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/docker-bootstrap.sh)"
```

This requires Docker only. Python, SWMM5, Node.js, MCP packages, and geospatial dependencies are already inside the image.

<details>
<summary>Advanced options</summary>

Run a specific container command:

```bash
docker run --rm -v "$PWD/agentic-swmm-runs:/app/runs" ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.3.0 acceptance
docker run --rm -v "$PWD/agentic-swmm-runs:/app/runs" ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.3.0 tecnopolo
docker run --rm -v "$PWD/agentic-swmm-runs:/app/runs" ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.3.0 uncertainty-dryrun
```

The fixed `v0.3.0` image packages Agentic SWMM tag `v0.3.0` and EPA SWMM tag `v5.2.4`. OpenClaw or Hermes can still orchestrate runs on top of this environment, but they are not required to reproduce the core benchmark artifacts.

Local developer install:

#### macOS / Linux

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.sh)"
```

#### Windows PowerShell

Run PowerShell as Administrator:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.ps1'))"
```

The macOS / Linux installer builds the SWMM solver from the official [USEPA/Stormwater-Management-Model](https://github.com/USEPA/Stormwater-Management-Model) source repository when needed. The Windows bootstrap installs Chocolatey if needed, Git, Python, Node.js LTS, SWMM, Python/MCP dependencies, and a `swmm5` command shim when required.

The Python environment covers the acceptance path and Tod Creek smoke-test dependencies, including `pandas`, `numpy`, `matplotlib`, `rasterio`, `pyshp`, `pysheds`, and `swmmtoolbox`.

For manual Python dependency installation, use:

```bash
pip install -r requirements.txt
```

<details>
<summary>Install after cloning instead</summary>

### Install after clone

```bash
bash scripts/install.sh --yes
source .venv/bin/activate
```

On Windows after cloning:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Yes
.\.venv\Scripts\Activate.ps1
```

</details>

</details>

## Why this project exists

Stormwater modelling is rarely a single command. A typical SWMM workflow may involve GIS preprocessing, rainfall formatting, parameter assignment, network assembly, INP construction, model execution, output checking, plotting, calibration, uncertainty analysis, and reporting.

These steps are often scattered across manual operations, scripts, notebooks, GIS software, and model files. That makes the workflow hard to rerun, hard to audit, and hard to trust.

AI agents can help coordinate these steps, but they also introduce a new risk: if generated files, assumptions, checks, and intermediate artifacts are not recorded, the modelling process becomes even harder to verify.

This repository provides a middle path:

**agentic orchestration with deterministic SWMM execution, explicit provenance, and verification-first modelling.**

## What makes it different

- **Five-minute onboarding:** install the workflow and SWMM engine with one bootstrap command.
- **Not just chat-to-SWMM:** agents coordinate the workflow, but model execution remains deterministic and inspectable.
- **Modular Skills:** GIS, climate, parameters, network, builder, runner, plotting, calibration, uncertainty, audit, and end-to-end orchestration are separated into reusable modules.
- **OpenClaw / Hermes ready:** the repository includes a public agent memory package and a top-level `swmm-end-to-end` orchestration skill for [OpenClaw](https://github.com/openclaw/openclaw), [Hermes Agent](https://github.com/NousResearch/hermes-agent), or compatible runtimes.
- **MCP-ready tools:** modules expose tool-level interfaces for agent orchestration where available.
- **Manifest-based provenance:** build, run, audit, and comparison stages emit traceable artifacts.
- **Verification-first workflow:** continuity, mass balance, preprocessing consistency, peak-flow parsing, and direct SWMM comparison are used before treating outputs as evidence.
- **Obsidian-compatible audit:** run artifacts can be converted into Markdown experiment notes for tracking project results, assumptions, evidence, and modelling memory over time.
- **Research-facing outputs:** generated notes, plots, summaries, and audit files are suitable for experiment tracking, collaboration, and publication workflows.
- **Works without an agent:** every core path can be run directly from the CLI.

## How the workflow works

<p align="center">
  <a href="docs/figs/openclaw_swmm_pipeline.pdf">
    <img src="docs/figs/openclaw_swmm_pipeline.png" alt="OpenClaw + SWMM agentic modelling pipeline with verification layer" style="background:#ffffff; padding:12px; border-radius:8px;" width="900" />
  </a>
</p>

*(Click the figure to open the PDF version.)*

The workflow is organized into six layers:

1. **Orchestrator layer** - optional OpenClaw or Hermes coordination.
2. **Skills layer** - SOP-style instructions for safe and reproducible tool use.
3. **MCP layer** - tool interfaces for GIS, climate, parameters, network, builder, runner, plot, calibration, and audit.
4. **Engine layer** - deterministic EPA SWMM execution through `swmm5`.
5. **Output layer** - standardized run folders with INP, RPT, OUT, plots, summaries, and manifests.
6. **Verification layer** - continuity, peak-flow, preprocessing, and direct SWMM consistency checks.

Agentic SWMM Workflow is not a chat-to-model-file wrapper. It is a reproducible and auditable modelling pipeline where agents coordinate the workflow, while SWMM execution, QA, provenance, and reporting remain deterministic and inspectable.

## What the workflow produces

Depending on the selected path, a run can produce:

- generated or supplied SWMM input files such as `model.inp`,
- SWMM report and binary outputs such as `.rpt` and `.out`,
- build and run manifests such as `manifest.json`,
- continuity and mass-balance QA summaries,
- parsed peak-flow metrics from SWMM report sections,
- rainfall-runoff figures,
- calibration and validation summaries,
- fuzzy uncertainty propagation summaries,
- `experiment_provenance.json`,
- `comparison.json`,
- Obsidian-compatible `experiment_note.md`.

## Obsidian-compatible audit memory

Agentic SWMM treats audit notes as a first-class interface, not as an afterthought. The audit layer turns run folders into Obsidian-compatible Markdown so users can manage modelling progress, assumptions, QA results, scenario comparisons, and project memory in a research notebook.

This is useful when a project grows beyond a single run:

- every run can leave an `experiment_note.md`,
- provenance and comparison records remain machine-readable,
- notes can be copied into an Obsidian vault with `--obsidian-dir`,
- successful, failed, and partial runs can all be documented without hiding missing evidence,
- calibration and uncertainty results can be tracked beside normal build/run/QA evidence.

The result is a workflow where OpenClaw or Hermes can help execute the modelling path while Obsidian can become the human-facing memory layer for project updates, experiment history, and research synthesis.

## Validation evidence

This repository includes two external benchmark paths that test different evidence boundaries.

### 1. Raw GeoPackage-to-INP benchmark

The TUFLOW SWMM Module 03 benchmark validates the structured raw GIS path. This is the stronger agentic workflow demonstration because it starts from public GeoPackage model layers and rebuilds the SWMM-ready structure before running QA and audit.

It converts public GeoPackage layers into SWMM-ready artifacts, including junctions, outfalls, conduits, subcatchments, raingages, multi-raingage rainfall inputs, `network.json`, `subcatchments.csv`, parameter JSON, a generated `model.inp`, SWMM outputs, QA summaries, and audit notes.

<p align="center">
  <img src="docs/figs/tuflow_swmm_module03_raw_layers.png" alt="TUFLOW SWMM Module 03 raw GeoPackage layers converted into Agentic SWMM subcatchments, conduits, junctions, and outfall" width="480" />
</p>

See `examples/tuflow-swmm-module03/README.md` for download instructions, expected artifacts, metrics, and the raw GeoPackage evidence boundary.

<details>
<summary>Run this benchmark</summary>

```bash
python3 scripts/benchmarks/run_tuflow_swmm_module03_raw_path.py
```

</details>

### 2. Prepared-input SWMM benchmark

The Tecnopolo benchmark validates the prepared-input path using an external **40-subcatchment** SWMM model derived from a public Zenodo dataset.

It checks that the workflow can execute an external SWMM model, compare workflow outputs against direct `swmm5` execution, inspect both an outfall and an internal junction, generate rainfall-runoff figures, and emit audit-ready artifacts.

<p align="center">
  <img src="docs/figs/tecnopolo_199401_outfall_rain_runoff.png" alt="Tecnopolo January 1994 rainfall-runoff benchmark at OUT_0" width="900" />
</p>

See `examples/tecnopolo/README.md` for validation details, expected peak-flow checks, reproducibility notes, and the prepared-input evidence boundary.

<details>
<summary>Run this benchmark</summary>

```bash
python3 scripts/benchmarks/run_tecnopolo_199401.py
```

</details>

### Additional runnable paths

The repository also includes an acceptance pipeline for regression checks and a minimal Tod Creek real-data fallback path for environments where the Tod Creek example inputs are available.

<details>
<summary>Run additional checks</summary>

```bash
python3 scripts/acceptance/run_acceptance.py --run-id latest
python3 scripts/real_cases/run_todcreek_minimal.py
```

</details>

### Evidence boundary

The current repository is strongest as a reproducible agentic workflow for prepared-input SWMM execution, structured raw GIS-to-INP benchmarks, QA, audit, plotting, calibration support, and uncertainty extension. It also provides a practical path for users to get running quickly and then grow toward richer case-specific modelling.

For fully greenfield watershed, subcatchment, and pipe-network generation directly from DEM, land use, soil, and drainage assets, the intended direction is to add case-specific delineation and parameterization evidence rather than overstate automatic generation before those examples are validated.

## Experiment audit example

The audit layer consolidates artifacts, QA checks, and metric provenance into an Obsidian-compatible experiment note. This example catches a recorded peak-flow value that does not match the value re-parsed from the SWMM report source section.

<p align="center">
  <img src="docs/figs/audit_comparison_example_readme.png" alt="Experiment audit comparison showing a peak-flow provenance mismatch" width="900" />
</p>

For agent-orchestrated runs, use a high-reasoning coding model and inspect the generated audit note before treating outputs as research evidence.

## Optional local verification

<details>
<summary>Acceptance, audit, and plot commands</summary>

```bash
python3 scripts/acceptance/run_acceptance.py --run-id latest
```

Check the acceptance report:

```text
runs/acceptance/latest/acceptance_report.md
```

Audit the run:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py --run-dir runs/acceptance/latest
```

Add `--obsidian-dir <vault-folder>` to write a copy of the same note into Obsidian.

Make a rainfall-runoff plot from acceptance outputs:

```bash
mkdir -p runs/acceptance/latest/07_plot
python3 skills/swmm-plot/scripts/plot_rain_runoff_si.py \
  --inp runs/acceptance/latest/04_builder/model.inp \
  --out runs/acceptance/latest/05_runner/acceptance.out \
  --out-png runs/acceptance/latest/07_plot/fig_rain_runoff.png
```

</details>

## End-to-end flow

1. Prepare deterministic inputs: GIS polygons, rainfall series, mapped soil/landuse parameters, and network schema/import.
2. Assemble a runnable SWMM `.inp` with `swmm-builder` and emit a build manifest.
3. Execute SWMM with `swmm-runner` and emit a run manifest plus parsed diagnostics.
4. Verify continuity, mass balance, preprocessing consistency, and extracted peak behavior.
5. Produce publication-style rainfall-runoff plots.
6. Optionally calibrate, validate, propagate fuzzy parameter uncertainty, and audit the run.

## Repository map

```text
agentic-swmm-workflow/
├─ README.md
├─ docs/
│  ├─ figs/openclaw_swmm_pipeline.{png,pdf}
│  ├─ experiment-audit-framework.md
│  ├─ openclaw-execution-path.md
│  └─ repo-map.md
├─ openclaw/
│  └─ memory/ (public agent memory package)
├─ examples/
│  ├─ todcreek/model_chicago5min.inp
│  ├─ tuflow-swmm-module03/
│  ├─ tecnopolo/
│  └─ calibration/
├─ scripts/
│  ├─ bootstrap.sh
│  ├─ bootstrap.ps1
│  ├─ install.sh
│  ├─ install.ps1
│  ├─ benchmarks/run_tecnopolo_199401.py
│  ├─ benchmarks/run_tuflow_swmm_module03_raw_path.py
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
│  ├─ swmm-uncertainty/
│  ├─ swmm-experiment-audit/
│  └─ swmm-end-to-end/
└─ runs/ (generated artifacts)
```

For more detail:
- See `docs/repo-map.md` for a broader repo walkthrough.
- See `skills/<module>/SKILL.md` for module-specific behavior and examples.
- Each module can expose an MCP server at `skills/<module>/scripts/mcp/server.js` for optional OpenClaw or Hermes integration.

## OpenClaw / Hermes orchestration

Install or configure an agent runtime first:

- [OpenClaw](https://github.com/openclaw/openclaw)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)

Then use this repository's public memory package and top-level skill as the Agentic SWMM context. If you want one agent-facing entrypoint instead of manually choosing module skills, start with:

```text
skills/swmm-end-to-end/SKILL.md
```

That top-level skill defines when to use the full modular path, when to use the prepared-input path, when to fall back to the Tod Creek minimal real-data runner, which QA gates must pass before a run is considered usable, and when to stop instead of inventing missing network or subcatchment inputs.

For out-of-the-box agent context in a public GitHub checkout, preload the project memory package before the top-level skill:

```text
openclaw/memory/
```

It contains identification, project soul, operational, ordered modelling workflow, evidence, and user-bridge memory files for OpenClaw, Hermes, or another compatible runtime. The memory package improves first-run behavior for repository users by teaching the agent what Agentic SWMM is, how to guide a user from input inventory through build/run/QA/audit, what it must not claim, how to choose workflow modes, how to use Obsidian-compatible audit notes as project memory, and how to communicate missing inputs and evidence boundaries. It does not depend on the maintainer's private local OpenClaw workspace.

For the exact MCP tool-call sequence behind that skill, see:

```text
docs/openclaw-execution-path.md
```

## Research extensions

The core repository focuses on reproducible SWMM execution, QA, audit, plotting, and agent orchestration. Research-facing modules for uncertainty and calibration build on the same run-directory, manifest, and Obsidian-compatible audit conventions.

### Fuzzy uncertainty propagation

`skills/swmm-uncertainty/` provides a framework for epistemic parameter uncertainty. Users can define triangular or trapezoidal membership functions, resolve alpha-cut intervals, sample parameter combinations, run SWMM, and summarize output envelopes by alpha level. It is designed as a research extension that can be audited alongside normal SWMM runs.

<details>
<summary>Dry-run example</summary>

```bash
python3 skills/swmm-uncertainty/scripts/uncertainty_propagate.py \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --fuzzy-space skills/swmm-uncertainty/examples/fuzzy_space.json \
  --config skills/swmm-uncertainty/examples/uncertainty_config.json \
  --run-root runs/uncertainty-demo \
  --summary-json runs/uncertainty-demo/uncertainty_summary.json \
  --dry-run
```

</details>

### Calibration support

`skills/swmm-calibration/` supports explicit candidate sets, bounded search, sensitivity scans, validation, and parameter scouting. It reuses manifest and run-directory conventions so calibration evidence can be audited alongside normal SWMM runs.

See `examples/calibration/README.md` for the compact calibration example.

## Where collaborators can help

Contributions are especially welcome in:
- additional SWMM case studies and benchmark datasets,
- stronger calibration and validation workflows,
- DEM, land-use, soil, and drainage-asset workflows for greenfield model generation,
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
- greenfield DEM/land-use/soil/drainage-asset-to-INP case studies,
- cleaner onboarding for external contributors,
- deeper reproducibility and equivalence testing.

## Citation

GitHub citation metadata is provided in `CITATION.cff`.

### APA (repository)
Zhang, Z., & Valeo, C. (2026). *agentic-swmm-workflow* [Computer software]. GitHub. https://github.com/Zhonghao1995/agentic-swmm-workflow

### APA (manuscript / preprint)
Zhang, Z., & Valeo, C. (2026). *Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw*. https://doi.org/10.31223/X5F47G
