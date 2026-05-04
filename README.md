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

**A five-minute, one-command EPA SWMM workflow that is auditable, memory-informed, and agent-ready.**

`agentic-swmm-workflow` helps researchers and developers install SWMM, run benchmark workflows, check outputs, audit evidence, keep Obsidian-ready modelling notes, and reuse lessons from previous runs. With OpenClaw, Hermes Agent, or a compatible agent runtime, users can describe a modelling goal in natural language while SWMM execution stays deterministic and explainable.

This is not a simple chat-to-SWMM wrapper. The agent can help coordinate the workflow, but the model files, SWMM runs, QA checks, plots, provenance, audit notes, and modelling memory remain visible as artifacts. The modeling-memory layer can notice repeated problems and propose skill refinements, but changes are accepted only after human review and benchmark verification.

Authors: **Zhonghao Zhang** & **Caterina Valeo**  
License: **MIT**

## 1. Try it in one command

Choose the path that matches what you want to do. :)

### Method 1. Docker (Recommend)

Use this when you want the most reproducible path and do not want to install Python packages, SWMM5, Node, or GIS dependencies locally. This runs the deterministic Agentic SWMM execution environment inside Docker and writes generated artifacts to `./agentic-swmm-runs/`.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/docker-bootstrap.sh)"
```

Requirements: Docker Desktop or Docker Engine.

### Method 2. MacOS / Linux local install

Use this when you want a local development environment. The installer clones or updates the repository, installs Python and MCP dependencies, and installs or builds `swmm5` if it is missing.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.sh)"
```

### Method 3. Windows PowerShell local install

Use this on Windows when you want a local development environment. Run PowerShell as Administrator. The installer sets up Git, Python, Node.js, SWMM, Python/MCP dependencies, and a `swmm5` command shim when needed.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.ps1'))"
```

## 2. Why this project exists

Stormwater modelling is rarely one command. A typical SWMM project may involve GIS preprocessing, rainfall formatting, parameter assignment, network assembly, INP construction, model execution, output checks, plots, calibration, uncertainty analysis, and reporting.

Those steps are often split across scripts, notebooks, GIS software, manual edits, and model files. Agentic AI can help coordinate them from natural language, but only if the evidence is kept visible. Generated files, assumptions, QA checks, warnings, and intermediate artifacts need to be recorded, not hidden behind a black box.

Agentic SWMM provides a middle path: natural-language orchestration with deterministic SWMM execution, explicit provenance, project memory, and verification-first modelling.

The memory layer matters because every modeller, catchment, dataset, and study goal is different. Each audited run can update Obsidian-ready notes and modeling-memory summaries, so the system can carry forward lessons about assumptions, failures, missing evidence, parser issues, and successful practices. Repeated patterns can become proposed skill refinements, but those proposals still require human review and benchmark verification.

**The goal is not to replace SWMM or the modeller, but to make SWMM-based modelling easier to rerun, inspect, remember, and trust.**

## 3. What makes it different

- **Five-minute onboarding:** install the workflow and SWMM engine with one bootstrap command.
- **Not just chat-to-SWMM:** agents coordinate the workflow, but model execution remains deterministic and inspectable.
- **Modular Skills:** GIS, climate, parameters, network, builder, runner, plotting, calibration, uncertainty, audit, and end-to-end orchestration are separated into reusable modules.
- **OpenClaw / Hermes ready:** the repository includes a public agent memory package and a top-level `swmm-end-to-end` orchestration skill for [OpenClaw](https://github.com/openclaw/openclaw), [Hermes Agent](https://github.com/NousResearch/hermes-agent), or compatible runtimes.
- **MCP-ready tools:** modules expose tool-level interfaces for agent orchestration where available.
- **Manifest-based provenance:** build, run, audit, and comparison stages emit traceable artifacts.
- **Verification-first workflow:** continuity, mass balance, preprocessing consistency, peak-flow parsing, and direct SWMM comparison are used before treating outputs as evidence.
- **Obsidian-compatible audit:** run artifacts can be converted into Markdown experiment notes for tracking project results, assumptions, evidence, and modelling memory over time.
- **Modeling memory:** audited run histories can be summarized into recurring failure patterns, assumptions, missing evidence, QA issues, lessons learned, and controlled skill refinement proposals.
- **Research-facing outputs:** generated notes, plots, summaries, and audit files are suitable for experiment tracking, collaboration, and publication workflows.
- **Works without an agent:** every core path can be run directly from the CLI.

## 4. How the workflow works

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

## 5. What the Agentic SWMM workflow produces

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

## 6. Obsidian-compatible audit memory

Agentic SWMM treats audit notes as a first-class interface, not as an afterthought. The audit layer turns run folders into Obsidian-compatible Markdown so users can manage modelling progress, assumptions, QA results, scenario comparisons, and project memory in a research notebook.

This is useful when a project grows beyond a single run:

- every run can leave an `experiment_note.md`,
- provenance and comparison records remain machine-readable,
- notes can be copied into an Obsidian vault with `--obsidian-dir`,
- successful, failed, and partial runs can all be documented without hiding missing evidence,
- calibration and uncertainty results can be tracked beside normal build/run/QA evidence.

The result is a workflow where OpenClaw or Hermes can help execute the modelling path while Obsidian can become the human-facing memory layer for project updates, experiment history, and research synthesis.

## 7. Modeling Memory and Controlled Skill Refinement

Agentic SWMM already records audit artifacts after runs through `swmm-experiment-audit`, including provenance, comparisons, QA checks, warnings, limitations, and Obsidian-compatible experiment notes.

The downstream `swmm-modeling-memory` skill summarizes historical audited runs. It can identify recurring failure patterns, repeated assumptions, missing evidence, QA issues, run-to-run differences, and successful practices. It can also generate proposed refinements for relevant workflow skills, such as end-to-end orchestration, audit reporting, QA verification, model building, or result parsing, plus a benchmark verification plan.

This layer does not allow fully autonomous self-editing. Skill update proposals are not evidence of correctness, and accepted skill changes require human review and benchmark verification.

```bash
python3 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs \
  --out-dir memory/modeling-memory
```

## 8. Validation evidence

This repository includes two external benchmark paths that test different evidence boundaries.

### 8.1 Raw GeoPackage-to-INP benchmark

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

### 8.2 Prepared-input SWMM benchmark

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

### 8.3 Additional runnable paths

The repository also includes an acceptance pipeline for regression checks and a minimal Tod Creek real-data fallback path for environments where the Tod Creek example inputs are available.

<details>
<summary>Run additional checks</summary>

```bash
python3 scripts/acceptance/run_acceptance.py --run-id latest
python3 scripts/real_cases/run_todcreek_minimal.py
```

</details>

### 8.4 Evidence boundary

The current repository is strongest as a reproducible agentic workflow for prepared-input SWMM execution, structured raw GIS-to-INP benchmarks, QA, audit, plotting, calibration support, and uncertainty extension. It also provides a practical path for users to get running quickly and then grow toward richer case-specific modelling.

For fully greenfield watershed, subcatchment, and pipe-network generation directly from DEM, land use, soil, and drainage assets, the intended direction is to add case-specific delineation and parameterization evidence rather than overstate automatic generation before those examples are validated.

## 9. Experiment audit example

The audit layer consolidates artifacts, QA checks, and metric provenance into an Obsidian-compatible experiment note. This example catches a recorded peak-flow value that does not match the value re-parsed from the SWMM report source section.

<p align="center">
  <img src="docs/figs/audit_comparison_example_readme.png" alt="Experiment audit comparison showing a peak-flow provenance mismatch" width="900" />
</p>

For agent-orchestrated runs, use a high-reasoning coding model and inspect the generated audit note before treating outputs as research evidence.

## 10. Optional local verification

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

## 11. End-to-end flow

1. Prepare deterministic inputs: GIS polygons, rainfall series, mapped soil/landuse parameters, and network schema/import.
2. Assemble a runnable SWMM `.inp` with `swmm-builder` and emit a build manifest.
3. Execute SWMM with `swmm-runner` and emit a run manifest plus parsed diagnostics.
4. Verify continuity, mass balance, preprocessing consistency, and extracted peak behavior.
5. Produce publication-style rainfall-runoff plots.
6. Optionally calibrate, validate, propagate fuzzy parameter uncertainty, and audit the run.

## 12. Repository map

```text
agentic-swmm-workflow/
├─ README.md
├─ docs/
│  ├─ figs/openclaw_swmm_pipeline.{png,pdf}
│  ├─ experiment-audit-framework.md
│  ├─ modeling-memory-and-skill-evolution.md
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
│  ├─ swmm-modeling-memory/
│  └─ swmm-end-to-end/
├─ memory/
│  └─ modeling-memory/ (generated modeling memory and proposals)
└─ runs/ (generated artifacts)
```

For more detail:
- See `docs/repo-map.md` for a broader repo walkthrough.
- See `skills/<module>/SKILL.md` for module-specific behavior and examples.
- Each module can expose an MCP server at `skills/<module>/scripts/mcp/server.js` for optional OpenClaw or Hermes integration.

## 13. OpenClaw / Hermes orchestration

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

## 14. Where collaborators can help

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

## 15. Ongoing research extensions (Fuzzy uncertainty propagation and Calibration support)

`skills/swmm-uncertainty/` provides a framework for epistemic parameter uncertainty. Users can define triangular or trapezoidal membership functions, resolve alpha-cut intervals, sample parameter combinations, run SWMM, and summarize output envelopes by alpha level. It is designed as a research extension that can be audited alongside normal SWMM runs. `skills/swmm-calibration/` supports explicit candidate sets, bounded search, sensitivity scans, validation, and parameter scouting. It reuses manifest and run-directory conventions so calibration evidence can be audited alongside normal SWMM runs.

See `examples/calibration/README.md` for the compact calibration example.

## 16. Citation

GitHub citation metadata is provided in `CITATION.cff`.

### APA (repository)
Zhang, Z., & Valeo, C. (2026). *agentic-swmm-workflow* [Computer software]. GitHub. https://github.com/Zhonghao1995/agentic-swmm-workflow

### APA (manuscript / preprint)
Zhang, Z., & Valeo, C. (2026). *Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw*. https://doi.org/10.31223/X5F47G
