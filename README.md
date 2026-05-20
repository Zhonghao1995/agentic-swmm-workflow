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

> **Alpha — pre-1.0, actively developed.** The CLI / Skill / MCP surface still evolves between minor versions; pin a version for reproducibility. See [Installation](docs/installation.md).

**Describe a stormwater modeling goal in plain language; get a reproducible, fully-audited EPA SWMM run.**

Agentic SWMM Workflow is an open-source, verification-first runtime for EPA SWMM. **aiswmm is the orchestrator**: you hand it a modeling goal in plain language, and it drives the full SWMM workflow — GIS preprocessing, INP assembly, solver runs, QA gates, plots, provenance, and modeling memory — calling its skills and MCP tools stage by stage. The SWMM solver itself is never modified and stays bit-for-bit deterministic; every artifact it produces lands on disk, inspectable and reusable. This is not a chat-to-SWMM black box — the modeller reviews each step and signs off on the result.

Authors: **Zhonghao Zhang** & **Caterina Valeo** · MIT · [Paper](https://doi.org/10.31223/X5F47G) · [Video](https://aiswmm.com/)

## Install

```bash
curl -fsSL https://aiswmm.com/install.sh | bash      # macOS / Linux
```

```powershell
irm https://aiswmm.com/install.ps1 | iex             # Windows PowerShell
```

Launch with `aiswmm`. Docker, Python-package, pinned-version paths, the full CLI reference, and how to review the install script first: [Installation & CLI guide](docs/installation.md). Keep API keys out of the `aiswmm` conversation — see [API key configuration](docs/api-key-configuration.md).

## What a run looks like

```
$ aiswmm "run tecnopolo_r1_199401.inp, audit it, and plot node OU2"

runs/2026-05-19/HHMMSS_tecnopolo_run/
├── model.rpt, model.out          SWMM native outputs
├── experiment_provenance.json    who / what / when + SHA-256
├── qa_summary.json               continuity checks
├── plots/OU2.png
└── final_report.md
```

Every run produces inspectable, reusable artifacts — not a chat transcript.

## Why

A real SWMM project is rarely one command: GIS preprocessing, rainfall formatting, parameter assignment, network assembly, INP construction, execution, QA, plots, calibration, uncertainty, reporting. Agentic SWMM gives a middle path — natural-language orchestration with deterministic SWMM execution, explicit provenance, and project memory. The goal is not to replace SWMM or the modeller, but to make SWMM modelling easier to **reproduce, audit, remember, and trust**.

## Workflow

<p align="center">
  <a href="docs/figs/modeling_memory_skill_evolution.png">
    <img src="docs/figs/modeling_memory_skill_evolution.png" alt="Agentic SWMM modeling memory and controlled skill evolution loop" style="background:#ffffff; padding:12px; border-radius:8px;" width="900" />
  </a>
</p>

Three connected layers — execution, modeling memory, and controlled skill evolution. Audited runs update human- and machine-readable memory; recurring patterns become skill-refinement proposals that still require human review and benchmark verification.

## Audit

<p align="center">
  <img src="docs/figs/audit_comparison_example_readme.png" alt="Experiment audit comparison showing a peak-flow provenance mismatch" width="900" />
</p>

The audit layer consolidates artifacts, QA checks, and metric provenance into an Obsidian-compatible note — here catching a recorded peak-flow value that disagrees with the value re-parsed from the SWMM report source.

## Use with Codex / Claude / OpenClaw / Hermes

aiswmm's SWMM capability is packaged as two portable folders — `skills/` and `mcp/`. To drive the workflow from an external agent runtime (Codex, Claude Code, OpenClaw, Hermes, …) you do **not** need to install the `aiswmm` package or its CLI — point the runtime at these two folders, with `skills/swmm-end-to-end/SKILL.md` as the entry skill.

Prerequisites still apply: each MCP server needs its Node dependencies (`npm install`), and the skill scripts need Python and the SWMM solver. Setup: [Skill installation](integrations/skills/README.md) · [MCP integration](integrations/mcp/README.md).

## Documentation

- [Installation & CLI guide](docs/installation.md) — Docker, local, Windows, and the full CLI verb reference
- [Validation evidence](docs/validation-evidence.md) — runnable benchmarks, commands, and evidence boundaries
- [LLM providers](docs/llm_providers.md) — OpenAI vs Claude subscription backends, auth, and switching
- [Memory runtime](docs/memory_runtime.md) — on-disk substrate, confidence quadrants, and opt-out flags
- [Experiment audit framework](docs/experiment-audit-framework.md) — provenance, comparison, and Obsidian note contracts
- [Modeling memory & skill evolution](docs/modeling-memory-and-skill-evolution.md) — the controlled memory-to-skill loop
- [Agent runtimes](docs/codex-runtime.md) — Codex / Claude / OpenClaw / Hermes orchestration paths
- [Repository map](docs/repo-map.md) — folder-level walkthrough

## Contributing

Contributions welcome: SWMM case studies, calibration and validation workflows, DEM / land-use / soil / drainage-asset workflows, new MCP tools, QA testing, tutorials, and interoperability with GIS, ML, and hydrologic toolchains.

Contact: zhonghaoz@uvic.ca · valeo@uvic.ca

## Citation

Zhang, Z., & Valeo, C. (2026). *Agentic SWMM: Auditable and Reproducible Stormwater Modelling Management System with Skills and Model Context Protocol*. https://doi.org/10.31223/X5F47G

GitHub citation metadata is in `CITATION.cff`. For the repository as software: Zhang, Z., & Valeo, C. (2026). *agentic-swmm-workflow* [Computer software]. https://github.com/Zhonghao1995/agentic-swmm-workflow
