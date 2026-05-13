# Repository Map

This public repository is the productized `aiswmm` runtime and public Agentic SWMM workflow package. It keeps the installable runtime, public skills, MCP adapters, examples, documentation, and curated evidence in separate layers.

## Organization Principle

`aiswmm` is the public entrypoint. The Python package provides the local runtime; skills define workflow-stage contracts; MCP servers expose selected skill scripts to agent runtimes; memory and validation artifacts keep evidence boundaries explicit.

Do not treat the public repository as a mirror of the private research workspace. Private raw data, private RAG memory, exploratory run histories, and immature methods should be promoted here only after they are small, documented, tested, and safe for public users.

## Top-Level Folders

| Folder | Role |
|---|---|
| `agentic_swmm/` | Python CLI/runtime for `aiswmm` and `agentic-swmm`, including planner, executor, registries, commands, and provider adapters. |
| `skills/` | Public workflow-stage skills, their contracts, Python scripts, examples, references, and tests where present. |
| `mcp/` | Repository-level MCP server adapters. Each server wraps selected scripts from the corresponding skill without duplicating the skill contract. |
| `agent/memory/` | Public startup memory package for Codex, OpenClaw, Hermes, and compatible agent runtimes. |
| `memory/modeling-memory/` | Generated example modeling memory derived from audited runs. This is not startup memory. |
| `examples/` | Small public examples and prepared input fixtures. |
| `scripts/` | Repository-level install, bootstrap, acceptance, benchmark, packaging, and MCP helper scripts. |
| `integrations/` | Instructions for registering skills and MCP servers with external agent runtimes. |
| `docs/` | User documentation, architecture notes, validation boundaries, release notes, and figures. |
| `runs/` | Local generated output area. Ordinary run artifacts should not be committed here. |
| `tests/` | Lightweight tests for the public runtime, skills, audit, memory, and packaging paths. |

## Runtime Layer

The user-facing runtime is:

```text
aiswmm / agentic-swmm
  -> agentic_swmm/cli.py
  -> agentic_swmm/commands/*
  -> agentic_swmm/agent/*
  -> skills + mcp + memory registries
```

The default `aiswmm` command starts the constrained local agent path. Direct subcommands such as `doctor`, `run`, `audit`, `plot`, `memory`, `skill`, and `mcp` remain available for deterministic use and testing.

## Skill And MCP Layers

Skills are grouped by workflow stage, not by every algorithm. New methods should usually become scripts, examples, or strategy options inside an existing stage skill before becoming a new public skill.

Current public skills:

| Skill | Main Question |
|---|---|
| `swmm-gis` | How are GIS/subcatchment inputs prepared for SWMM? |
| `swmm-network` | How are junctions, conduits, outfalls, network QA, and INP network sections handled? |
| `swmm-params` | How are land-use and soil inputs mapped to SWMM parameters? |
| `swmm-climate` | How is rainfall formatted for SWMM? |
| `swmm-builder` | How is a SWMM INP assembled from prepared artifacts? |
| `swmm-runner` | How is SWMM executed and parsed reproducibly? |
| `swmm-plot` | How are rainfall-runoff and related figures generated? |
| `swmm-calibration` | Which parameters best match observations? |
| `swmm-uncertainty` | How much output spread follows from uncertain inputs? |
| `swmm-experiment-audit` | What happened in one run, and what evidence supports it? |
| `swmm-modeling-memory` | What keeps happening across audited runs? |
| `swmm-end-to-end` | Which module should run next in an agent-orchestrated workflow? |

Current public MCP servers:

```text
mcp/swmm-builder/
mcp/swmm-calibration/
mcp/swmm-climate/
mcp/swmm-gis/
mcp/swmm-network/
mcp/swmm-params/
mcp/swmm-plot/
mcp/swmm-runner/
```

The public MCP layer intentionally exposes eight stable module adapters. Private-only skills such as private RAG memory or immature research skills should not be added here until they are explicitly promoted for public users.

## Memory And Audit Layers

There are two repository memory layers:

| Layer | Path | Job |
|---|---|---|
| Agent preload memory | `agent/memory/` | Gives compatible agent runtimes stable public project identity, operating posture, and evidence boundaries. |
| Agentic SWMM modeling memory | `memory/modeling-memory/` | Summarizes audited SWMM runs, repeated issues, and controlled skill update proposals. |

The audit layer sits before modeling memory:

```text
SWMM run -> swmm-experiment-audit -> experiment_provenance.json / comparison.json / experiment_note.md -> swmm-modeling-memory
```

Audit records are evidence for a run. Modeling memory is a summary of repeated patterns. Neither one proves a scientific claim by itself.

## Evidence Boundary

The public repository is strongest as a reproducible, auditable runtime for:

- prepared-input SWMM execution;
- structured GIS-to-INP benchmark paths;
- plotting, QA, audit, and provenance generation;
- calibration and uncertainty scaffolds;
- public memory and skill contracts for agent runtimes.

Do not overstate it as fully automatic greenfield watershed and pipe-network generation unless a case-specific public benchmark has validated those inputs, outputs, and QA checks.
