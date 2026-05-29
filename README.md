# Agentic SWMM Workflow

<p align="center">
  <img src="docs/figs/agentic_swmm_logo.png" alt="Agentic SWMM logo with agentic robot, stormwater system, and SWMM wordmark" width="900" />
</p>

<p align="center">
  <a href="https://aiswmm.com/demo/">
    <img src="https://img.shields.io/badge/demo-live-1a7f37" alt="Demo Live — aiswmm.com/demo" />
  </a>
  <a href="https://github.com/Zhonghao1995/agentic-swmm-workflow/actions/workflows/ci.yml">
    <img src="https://github.com/Zhonghao1995/agentic-swmm-workflow/actions/workflows/ci.yml/badge.svg" alt="CI status" />
  </a>
  <a href="https://github.com/Zhonghao1995/agentic-swmm-workflow/releases/latest">
    <img src="https://img.shields.io/github/v/release/Zhonghao1995/agentic-swmm-workflow?label=release&color=1F6FEB" alt="latest release" />
  </a>
  <a href="https://pypi.org/project/aiswmm/">
    <img src="https://img.shields.io/pypi/v/aiswmm?label=PyPI&color=3775A9&cacheSeconds=300" alt="PyPI version" />
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

> **Pre-1.0** · stable **v0.7.1** · `pip install aiswmm==0.7.1` · [CHANGELOG](CHANGELOG.md)


**Agentic SWMM for reproducible stormwater modeling**<br>
*[**aiswmm**](https://pypi.org/project/aiswmm/) runtime + Skills + MCP + SWMM + verification-first workflow + Obsidian-compatible audit · also compatible with [Codex](https://openai.com/codex/), [OpenClaw](https://github.com/openclaw/openclaw), and [Hermes Agent](https://github.com/NousResearch/hermes-agent).*

**A five-minute, one-command EPA SWMM workflow that is auditable, memory-informed, and agent-ready.**

** Try our Live Demo at [aiswmm.com](https://aiswmm.com/demo/) **

## Project Overview

Agentic SWMM Workflow is an open-source, verification-first framework for reproducible stormwater modeling with EPA SWMM. It supports automated execution, QA checks, provenance tracking, calibration support, documentation, and modeling memory, while keeping human modelers in control.

**The goal is not to replace SWMM or the modeller, but to make SWMM-based modelling easier to reproduce, audit, remember, and trust.** Agentic SWMM comes with *aiswmm* as its built-in runtime. Users can describe a modeling goal in natural language, while SWMM execution remains deterministic, inspectable, and artifact-based. The repository's MCP servers and Skills can also be used with other agent runtimes, including Codex, Claude, OpenClaw, and Hermes.

This is not a simple chat-to-SWMM wrapper. The aiswmm runtime can help coordinate the workflow, but model files, SWMM runs, QA checks, plots, provenance records, audit notes, and modeling memory remain visible as reusable artifacts. Modeling memory can summarize repeated problems and propose Skill refinements, but accepted changes still require human review and benchmark verification.

Authors: **Zhonghao Zhang** & **Caterina Valeo**  
License: **MIT**

<p>
  <a href="https://aiswmm.com/"><img src="https://img.shields.io/badge/Video-introduction-EA4335" alt="Introduction video" /></a>
  <a href="https://doi.org/10.31223/X5F47G"><img src="https://img.shields.io/badge/Preprint-EarthArXiv-1F6FEB" alt="Preprint on EarthArXiv" /></a>
  <a href="https://doi.org/10.5281/zenodo.20337281"><img src="https://img.shields.io/badge/Zenodo-DOI-1682D4" alt="Zenodo DOI" /></a>
</p>


## Why this project exists

Stormwater modelling is rarely one command. A typical SWMM project can involve GIS preprocessing, rainfall formatting, parameter assignment, network assembly, INP construction, model execution, QA checks, plots, calibration, uncertainty analysis, and reporting.

Agentic SWMM provides a middle path: natural-language orchestration with deterministic SWMM execution, explicit provenance, project memory, and verification-first modelling.

## What makes it different

- **Quick onboarding:** start from one-line macOS/Linux or Windows installers, with Docker and Python package paths documented separately.
- **Agent-guided, SWMM-grounded:** agents can coordinate tasks, while model execution stays deterministic, inspectable, and CLI-runnable.
- **Modular skill layer:** GIS, climate, building, running, plotting, calibration, uncertainty, audit, and orchestration are separated into reusable modules with MCP interfaces where available.
- **Verification-first provenance:** build, run, audit, and comparison stages emit traceable artifacts before outputs are treated as evidence.
- **Supervised skill evolution:** audited runs can surface recurring workflow patterns and propose updates to existing skills or new skills, while staying coupled to the current skill-driven framework.

## Meet your agent in about five minutes

> **Quick try.** The one-line installer below installs the current development build — fine for kicking the tires. **For reproducible or production use, the pinned Docker image (`:v0.7.1`) is the recommended path** (v0.6.4 remains pinned for companion-paper reproducibility) — see [runtime install options](docs/runtime-install-options.md).

macOS and Linux:

```bash
curl -fsSL https://aiswmm.com/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://aiswmm.com/install.ps1 | iex
```

After installation, launch the runtime with `aiswmm`.

One-line installers run a remote script — review it first if you want to see what executes. The installer can set up your OpenAI API key, or you can configure one later via your shell; see [API key configuration](docs/api-key-configuration.md). Never paste API keys into the `aiswmm` conversation.

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
| [Cross-environment byte-identical reproducibility](docs/byte-identical-reproducibility.md) | A natural-language prompt (`Run the Tecnopolo (Rome 1994) demo`) drives the aiswmm chain (LLM agent → MCP → swmm-runner skill) to the same byte-identical `model.out` as bare `swmm5`, across macOS and Docker. **v0.7.1 re-verification**: the minimum natural-language prompt length for this chain is now **11 words**, and the `model.out` SHA256 remains identical across the v0.7.0 → v0.7.1 minor revision. | SWMM execution-layer reproducibility, not agentic workflow reproducibility |
| [LLM-driven dispatch + data-scarce urban modeling (SWMManywhere)](docs/v0.7.1-swmmanywhere-nl-driven-evidence.md) | A single natural-language sentence referring only to a WGS84 bounding box drives the end-to-end SWMManywhere → SWMM → audit → network-map workflow on two independent regions (Greenwich Peninsula and NYC Midtown, ~1 km² each) — no shapefile, no DEM file, no step-by-step tool instructions. Synthesis is the work of [SWMManywhere](https://github.com/ImperialCollegeLondon/SWMManywhere) (Imperial College London, BSD-3-Clause). | Agent-side plumbing for data-scarce baseline modeling; **not** a calibrated or validated network. Calibration is next-milestone scope. |
| [Cross-session memory autonomous activation](docs/v0.7.1-cross-session-memory-evidence.md) | An 11-word user prompt drove a complete Tecnopolo run on 2026-05-28 during which the LLM autonomously queried `recall_session_history` and recovered two prior Tecnopolo sessions from 12 days earlier — the first user-observable activation of the memory layer on a real production run. | Memory layer fires correctly and shapes planner decisions; staleness weighting and negative-precedent handling are next-milestone scope. |

Examples: [TUFLOW](examples/tuflow-swmm-module03/README.md) and [Tecnopolo](examples/tecnopolo/README.md).

## Audit and research memory

The audit layer consolidates artifacts, QA checks, and metric provenance into an Obsidian-compatible experiment note. This example catches a recorded peak-flow value that does not match the value re-parsed from the SWMM report source section.

<p align="center">
  <img src="docs/figs/audit_comparison_example_readme.png" alt="Experiment audit comparison showing a peak-flow provenance mismatch" width="900" />
</p>

The downstream modelling-memory layer can summarize audited run histories into recurring failure patterns, assumptions, missing evidence, QA issues, lessons learned, and controlled proposals for updating existing skills or creating new skills. Because skills drive the workflow, these proposals stay coupled to the current Agentic SWMM framework and still require human review and benchmark verification before acceptance.

More details: [Experiment audit framework](docs/experiment-audit-framework.md) and [Modeling memory and skill evolution](docs/modeling-memory-and-skill-evolution.md).

## Codex / Claude / OpenClaw / Hermes ready

Beyond its own aiswmm runtime, the Agentic SWMM workflow can be driven by external agent runtimes — Codex, Claude Code, OpenClaw, or Hermes. For an agent-orchestrated run, preload the `agent/memory/` package and point the runtime at the top-level entry skill `skills/swmm-end-to-end/SKILL.md`, which decides which workflow path to take, which QA gates must pass, and when to stop rather than invent missing inputs.

More details: [Codex runtime path](docs/codex-runtime.md) · [OpenClaw execution path](docs/openclaw-execution-path.md) · [Skill installation](integrations/skills/README.md) · [MCP runtime integration](integrations/mcp/README.md).

## Documentation map

- [Validation evidence](docs/validation-evidence.md) - benchmark scope, commands, audit example, and evidence boundaries
- [Installation and CLI guide](docs/installation.md) - Docker, local install, Windows options, and CLI examples
- [LLM providers](docs/llm_providers.md) - OpenAI (default, via `aiswmm login`) vs opt-in Anthropic backend, both API-key + standard function-calling, auth, and how to switch
- [Experiment audit framework](docs/experiment-audit-framework.md) - provenance, comparison, and Obsidian note contracts
- [Modeling memory and skill evolution](docs/modeling-memory-and-skill-evolution.md) - controlled memory-to-skill refinement loop
- [Memory runtime](docs/memory_runtime.md) - on-disk substrate, four confidence quadrants, and runtime opt-out flags
- [Memory runtime CLI examples](docs/memory_runtime_cli.md) - one worked example per memory verb
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
