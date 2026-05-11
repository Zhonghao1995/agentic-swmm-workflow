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
  <a href="https://pypi.org/project/aiswmm/">
    <img src="https://img.shields.io/pypi/v/aiswmm?label=PyPI&color=3775A9&cacheSeconds=300" alt="PyPI version" />
  </a>
  <a href="https://github.com/Zhonghao1995/agentic-swmm-workflow/releases">
    <img src="https://img.shields.io/github/downloads/Zhonghao1995/agentic-swmm-workflow/total?label=release%20downloads&color=0E8A16" alt="GitHub Release downloads" />
  </a>
  <a href="https://codecov.io/gh/Zhonghao1995/agentic-swmm-workflow">
    <img src="https://codecov.io/gh/Zhonghao1995/agentic-swmm-workflow/graph/badge.svg" alt="Codecov coverage" />
  </a>
  <a href="https://github.com/Zhonghao1995/agentic-swmm-workflow/pkgs/container/agentic-swmm-workflow">
    <img src="https://img.shields.io/badge/docker-reproducible-2496ED" alt="Docker reproducible environment" />
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license" />
  </a>
  <a href="https://zhonghaoz.ca">
    <img src="https://img.shields.io/badge/built%20by-Zhonghao-7C3AED" alt="Built by Zhonghao" />
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

Video: [*Agentic SWMM workflow: introduction and workflow explanation*](https://aiswmm.com/)

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

## Try it in one command

Recommended Docker path:

```bash
mkdir -p agentic-swmm-runs && docker run --rm -v "$PWD/agentic-swmm-runs:/app/runs" ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.6.0 acceptance
```

Docker writes artifacts to `agentic-swmm-runs`. Local installation is also available for macOS, Linux, and Windows, but review the install script before running it. Details: [Installation and CLI guide](docs/installation.md).

Python package:

```bash
pip install aiswmm
aiswmm setup --provider openai --model gpt-5.5
aiswmm --help
```

You can choose another OpenAI model at setup time, such as `gpt-5.4` or `gpt-5.5`.

Full local runtime installer:

```bash
curl -fsSL https://aiswmm.com/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://aiswmm.com/install.ps1 | iex
```

Release notes for the interactive `aiswmm` runtime milestone: [v0.6.0 release README](docs/releases/v0.6.0.md).

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

The repository includes runnable benchmarks and research previews with different evidence boundaries. The README keeps only the index; figures, commands, and boundary notes live in [Validation evidence](docs/validation-evidence.md).

| Path | What it shows | Evidence boundary |
| --- | --- | --- |
| [Information-loss-guided subcatchment partition](docs/validation-evidence.md#information-loss-guided-subcatchment-partition) | QGIS-to-Agentic SWMM preprocessing using entropy and fuzzy-similarity concepts from Zhang & Valeo's [Journal of Hydrology paper](https://doi.org/10.1016/j.jhydrol.2025.134447) | GIS preprocessing concept, not a calibrated SWMM performance claim |
| [Raw GeoPackage-to-INP benchmark](docs/validation-evidence.md#raw-geopackage-to-inp-benchmark) | Public TUFLOW GeoPackage layers converted into SWMM-ready artifacts, QA, and audit | Structured raw GIS path, not arbitrary CAD/GIS recognition |
| [Prepared-input SWMM benchmark](docs/validation-evidence.md#prepared-input-swmm-benchmark) | External 40-subcatchment Tecnopolo model execution, plotting, and direct `swmm5` comparison | Prepared INP validation path |
| [Prior Monte Carlo uncertainty smoke](docs/validation-evidence.md#prior-monte-carlo-uncertainty-smoke) | Tecnopolo HORTON parameter perturbation and hydrograph envelope preview | Prior uncertainty smoke, not calibration |
| [Optional INP-derived raw adapter benchmark](docs/validation-evidence.md#inp-derived-raw-adapter-benchmark) | Raw-like inputs extracted from a public SWMM fixture and rebuilt through the modular path | Adapter handoff check, not greenfield watershed generation |

Examples: [TUFLOW](examples/tuflow-swmm-module03/README.md) and [Tecnopolo](examples/tecnopolo/README.md).

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

For agent-orchestrated runs, preload the Agentic AI memory package and then use the top-level end-to-end skill:

```text
agentic-ai/memory/
skills/swmm-end-to-end/SKILL.md
```

The top-level skill defines when to use the full modular path, when to use the prepared-input path, which QA gates must pass, and when to stop instead of inventing missing inputs.

For common prepared-input execution, audit, plotting, and memory summarization, the skill should prefer the unified `agentic-swmm` CLI. MCP tools remain available for the modular stages and for agent runtimes that need fine-grained tool calls.

More details: [Codex runtime path](docs/codex-runtime.md), [OpenClaw execution path](docs/openclaw-execution-path.md), [Skill installation](integrations/skills/README.md), and [MCP runtime integration](integrations/mcp/README.md).

## Documentation map

- [Validation evidence](docs/validation-evidence.md) - benchmark scope, commands, audit example, and evidence boundaries
- [Installation and CLI guide](docs/installation.md) - Docker, local install, Windows options, and CLI examples
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
