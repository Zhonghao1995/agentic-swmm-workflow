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
  <a href="#one-command-options">
    <img src="https://img.shields.io/badge/install-options-0B74DE" alt="install options" />
  </a>
  <a href="#try-it-with-docker">
    <img src="https://img.shields.io/badge/docker-reproducible-2496ED" alt="Docker reproducible environment" />
  </a>
  <a href="https://github.com/USEPA/Stormwater-Management-Model">
    <img src="https://img.shields.io/badge/SWMM-5.2-blue" alt="EPA SWMM 5.2" />
  </a>
  <a href="#codex--openclaw--hermes-ready">
    <img src="https://img.shields.io/badge/Codex%20%2F%20OpenClaw%20%2F%20Hermes-ready-1F6FEB" alt="Codex, OpenClaw, or Hermes ready" />
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license" />
  </a>
</p>

**Agentic SWMM for reproducible stormwater modeling**<br>
*[Codex](https://openai.com/codex/), [OpenClaw](https://github.com/openclaw/openclaw), or [Hermes Agent](https://github.com/NousResearch/hermes-agent) + Skills + MCP + SWMM + verification-first workflow + Obsidian-compatible audit*

**A five-minute EPA SWMM workflow that is auditable, memory-informed, and agent-ready.**

Agentic SWMM Workflow is an open-source, verification-first framework for reproducible stormwater modeling with EPA SWMM. It supports automated execution, QA checks, provenance tracking, calibration support, documentation, and modeling memory, while keeping human modelers in control.

The project is designed to work with agent runtimes such as Codex, OpenClaw, or Hermes. Users can describe a modeling goal in natural language, while SWMM execution remains deterministic, inspectable, and artifact-based.

This is not a simple chat-to-SWMM wrapper. The agent can help coordinate the workflow, but model files, SWMM runs, QA checks, plots, provenance records, audit notes, and modeling memory remain visible as reusable artifacts. Modeling memory can summarize repeated problems and propose skill refinements, but accepted changes still require human review and benchmark verification.

Authors: **Zhonghao Zhang** & **Caterina Valeo**  
License: **MIT**

Paper: [*Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw*](https://doi.org/10.31223/X5F47G)

## Why this project exists

Stormwater modelling is rarely one command. A typical SWMM project can involve GIS preprocessing, rainfall formatting, parameter assignment, network assembly, INP construction, model execution, QA checks, plots, calibration, uncertainty analysis, and reporting.

Agentic SWMM provides a middle path: natural-language orchestration with deterministic SWMM execution, explicit provenance, project memory, and verification-first modelling.

**The goal is not to replace SWMM or the modeller, but to make SWMM-based modelling easier to rerun, inspect, remember, and trust.**

## What makes it different

- **Quick onboarding:** start from an explicit Docker run, or use local bootstrap scripts after reviewing them.
- **Agent-guided, SWMM-grounded:** agents can coordinate tasks, while model execution stays deterministic, inspectable, and CLI-runnable.
- **Modular skill layer:** GIS, climate, building, running, plotting, calibration, uncertainty, audit, and orchestration are separated into reusable modules with MCP interfaces where available.
- **Verification-first provenance:** build, run, audit, and comparison stages emit traceable artifacts before outputs are treated as evidence.
- **Supervised skill evolution:** audited runs can surface recurring workflow patterns and propose updates to existing skills or new skills, while staying coupled to the current skill-driven framework.

## Try it with Docker

For the reproducible path, install Docker Desktop or Docker Engine, then run:

```bash
mkdir -p agentic-swmm-runs
docker run --rm -v "$PWD/agentic-swmm-runs:/app/runs" ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.3.0 acceptance
```

Artifacts are written to `agentic-swmm-runs`.

## One command options

The repository also includes one-command setup scripts for local installs. Review the script first, then run it if you are comfortable with what it does.

<details>
<summary>macOS / Linux local install</summary>

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.sh)"
```

</details>

<details>
<summary>Windows PowerShell local install</summary>

Run PowerShell as Administrator.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.ps1'))"
```

</details>

## Workflow

<p align="center">
  <a href="docs/figs/modeling_memory_skill_evolution.png">
    <img src="docs/figs/modeling_memory_skill_evolution.png" alt="Agentic SWMM modeling memory and controlled skill evolution loop" style="background:#ffffff; padding:12px; border-radius:8px;" width="900" />
  </a>
</p>

The workflow has three connected layers: execution, modeling memory, and controlled skill evolution. Natural-language requests can trigger reproducible SWMM actions; audited artifacts update human-readable and machine-readable memory; repeated patterns can produce skill-refinement proposals that still require human review and benchmark verification.

## What a run can produce

- generated or supplied SWMM input files such as `model.inp`
- SWMM report and binary outputs such as `.rpt` and `.out`
- manifests, command traces, QA summaries, and parsed peak-flow metrics
- rainfall-runoff figures, calibration summaries, and fuzzy uncertainty summaries
- audit records: `experiment_provenance.json`, `comparison.json`, and `experiment_note.md`
- Obsidian-ready modelling notes and modelling-memory summaries

## Validation snapshot

The repository includes benchmark paths with different evidence boundaries.

### Information-loss-guided subcatchment partition

This preview shows a QGIS-to-Agentic SWMM preprocessing path that partitions the TodCreek watershed before SWMM open-channel simulation using entropy theory, rather than only fixed area thresholds. This innovative spatial discretization method reduces information loss during simplification, improves the representation of spatial heterogeneity, and preserves SWMM’s model structure.

<p align="center">
  <img src="docs/figs/information_entropy_subcatchment_partition_readme.png" alt="Entropy and fuzzy-similarity guided subcatchment partition showing alternative watershed discretizations before SWMM simulation" width="900" />
</p>

Scientific basis: Zhang, Z., & Valeo, C. (2026), [*Quantifying uncertainty in flowrate modelling using spatially defined fuzzy entropy based on hydrological processes in a catchment*](https://doi.org/10.1016/j.jhydrol.2025.134447), *Journal of Hydrology*, 664, 134447.

Evidence boundary: this figure demonstrates the GIS preprocessing and information-loss-based subcatchment partition concept. It is not a calibrated SWMM performance claim.

### Raw GeoPackage-to-INP benchmark

This path converts public TUFLOW SWMM Module 03 GeoPackage layers into SWMM-ready artifacts before running QA and audit.

<p align="center">
  <img src="docs/figs/tuflow_swmm_module03_raw_layers.png" alt="TUFLOW SWMM Module 03 raw GeoPackage layers converted into Agentic SWMM subcatchments, conduits, junctions, and outfall" width="520" />
</p>

```bash
python3 scripts/benchmarks/run_tuflow_swmm_module03_raw_path.py
```

### Prepared-input SWMM benchmark

This path validates execution, direct SWMM comparison, node-level QA, plotting, and audit artifacts for an external 40-subcatchment SWMM model from the public Tecnopolo dataset. The layout figure is generated from the prepared INP coordinates, conduits, outfalls, rain gage, and subcatchment routing fields; the hydrograph is checked against a direct `swmm5` baseline.

<p align="center">
  <img src="docs/figs/tecnopolo_prepared_layout.png" alt="Tecnopolo prepared-input SWMM layout with 40 subcatchments routed through junctions, conduits, outfalls, and one rain gage" width="760" />
</p>

<p align="center">
  <img src="docs/figs/tecnopolo_199401_outfall_rain_runoff.png" alt="Tecnopolo January 1994 rainfall-runoff benchmark at OUT_0" width="900" />
</p>

```bash
python3 scripts/benchmarks/run_tecnopolo_199401.py
```

### Optional INP-derived raw adapter benchmark

This path fetches a public `generate_swmm_inp` fixture, extracts raw-like GIS/CSV inputs from its open SWMM input file, and runs the Agentic SWMM modular path from those derived inputs.

```bash
python3 scripts/benchmarks/run_generate_swmm_inp_raw_path.py
```

More details: [Validation evidence](docs/validation-evidence.md), [TUFLOW example](examples/tuflow-swmm-module03/README.md), and [Tecnopolo example](examples/tecnopolo/README.md).

## Audit and research memory

The audit layer consolidates artifacts, QA checks, and metric provenance into an Obsidian-compatible experiment note. This example catches a recorded peak-flow value that does not match the value re-parsed from the SWMM report source section.

<p align="center">
  <img src="docs/figs/audit_comparison_example_readme.png" alt="Experiment audit comparison showing a peak-flow provenance mismatch" width="900" />
</p>

The downstream modelling-memory layer can summarize audited run histories into recurring failure patterns, assumptions, missing evidence, QA issues, lessons learned, and controlled proposals for updating existing skills or creating new skills. Because skills drive the workflow, these proposals stay coupled to the current Agentic SWMM framework and still require human review and benchmark verification before acceptance.

More details: [Experiment audit framework](docs/experiment-audit-framework.md) and [Modeling memory and skill evolution](docs/modeling-memory-and-skill-evolution.md).

## Codex / OpenClaw / Hermes ready

Codex can serve as the primary local development runtime for this repository: it can inspect the checkout, run scripts, edit skills, generate audit records, update the local Obsidian vault, and review evidence before claims are accepted.

OpenClaw and Hermes remain compatible orchestration targets, especially for MCP-centered agent runs outside the Codex development environment.

For agent-orchestrated runs, preload the public memory package and then use the top-level end-to-end skill:

```text
openclaw/memory/
skills/swmm-end-to-end/SKILL.md
```

The top-level skill defines when to use the full modular path, when to use the prepared-input path, which QA gates must pass, and when to stop instead of inventing missing inputs.

More details: [Codex runtime path](docs/codex-runtime.md) and [OpenClaw execution path](docs/openclaw-execution-path.md).

## Documentation map

- [Validation evidence](docs/validation-evidence.md) - benchmark scope, commands, audit example, and evidence boundaries
- [Experiment audit framework](docs/experiment-audit-framework.md) - provenance, comparison, and Obsidian note contracts
- [Modeling memory and skill evolution](docs/modeling-memory-and-skill-evolution.md) - controlled memory-to-skill refinement loop
- [Codex runtime path](docs/codex-runtime.md) - local development, audit, Obsidian, and evidence-review workflow
- [OpenClaw execution path](docs/openclaw-execution-path.md) - MCP tool-call sequence for agent runtimes
- [Repository map](docs/repo-map.md) - folder-level walkthrough
- [Calibration example](examples/calibration/README.md) - compact calibration support example

## Where collaborators can help

Contributions are welcome in additional SWMM case studies, stronger calibration and validation workflows, DEM / land-use / soil / drainage-asset workflows, new MCP tools, QA testing, tutorials, and interoperability with GIS, ML, and hydrologic toolchains.

Contact:
- zhonghaoz@uvic.ca
- valeo@uvic.ca

## Citation

GitHub citation metadata is provided in `CITATION.cff`.

### APA repository
Zhang, Z., & Valeo, C. (2026). *agentic-swmm-workflow* [Computer software]. GitHub. https://github.com/Zhonghao1995/agentic-swmm-workflow

### APA manuscript / preprint
Zhang, Z., & Valeo, C. (2026). *Agentic Modelling Pipeline: Reproducible Rapid Stormwater Modelling Management System with OpenClaw*. https://doi.org/10.31223/X5F47G
