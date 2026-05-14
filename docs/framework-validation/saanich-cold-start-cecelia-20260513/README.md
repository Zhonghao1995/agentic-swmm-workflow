# Cold-start agent validation — Saanich Cecelia Ravine (2026-05-13)

Companion to `../saanich-smoke-20260513/`. This directory captures the
evidence of an **independent** cold-start agent run, executed under
isolation constraints to test whether the skill+MCP framework is
self-sufficient for a fresh agent that has never seen the repo before.

## Setup

- **Subagent type:** `general-purpose` (Claude, fresh session, no shared
  context with the operator who produced the earlier `saanich-smoke-20260513`
  evidence).
- **Natural-language prompt given** (the agent's only instruction):
  > 我有 data/Saanich/ 里的 GIS 数据和 1984 年雨量数据。能帮我用这些做一个
  > SWMM 模型并跑通，最后画一张出水口流量随时间变化的图吗？
- **Read constraints (enforced via prompt):** the subagent was forbidden
  from reading anything under `runs/` or `docs/framework-validation/`. It
  could read everything else (`skills/`, `mcp/`, `tests/`, `data/`,
  `scripts/`, other `docs/`).
- **Time budget:** ~30 minutes.

## What the cold-start agent decided on its own

- **Skill routing:** picked `swmm-end-to-end` correctly (it considered
  `swmm-modeling-memory` and `swmm-experiment-audit` and rejected both).
- **Mode:** chose **Mode A (Full modular build)** rather than Mode 0 (the
  smoke mode this repo's other run was wired for). The agent's reasoning:
  Saanich is a raw municipal dataset with no curated artefacts, so the
  full modular path was a closer match.
- **Basin selection:** picked `Cecelia Ravine` (OBJECTID=125, ~31 035 m²,
  11 pipes, 10 manholes), different from the operator's baseline
  (`Colquitz River`, OBJECTID=100). Both are valid; the divergence
  itself is informative — both basins lead to a runnable model.

## What got produced

| Artefact | File |
|---|---|
| SWMM INP (3 subcatchments, 12 junctions, 1 outfall `OF1`, 11 conduits) | `model.inp` |
| SWMM .rpt | `model.rpt` |
| Runner manifest with metrics | `runner_manifest.json` |
| Outfall flow time-series plot | `outfall_flow.png` |

**Runtime metrics:** SWMM 5.2.4 exit 0, runoff continuity error 0.000%,
flow routing continuity 0.0%, total precipitation 11.083 mm, total
runoff 6.075 mm over Apr–Sep 1984. Peak outfall inflow appears as
~1.8 × 10⁻⁴ m³/s in the plot but rounds to 0.000 in `swmm_peak` because
the .rpt summary block uses 3-decimal display precision (a known minor
finding consistent with the baseline run).

## What the cold-start agent had to do that the framework didn't help with

The agent succeeded but only by writing **5 hand-rolled adapter scripts**
to bridge gaps that the framework leaves to the operator:

1. Saanich `StormGravityMain.shp` + `StormManhole.shp` → `pipes.csv` /
   `outfalls.csv` / `mapping.json` in the format `city_network_adapter`
   expects (no MCP tool covers this conversion).
2. Pipe re-orientation: `city_network_adapter` assumes pipe geometry
   direction equals flow direction; Saanich pipes do not satisfy this.
   The first `network qa` returned 12 `no_outfall_path` warnings. The
   agent wrote a BFS-from-outfall reorientation script and re-imported.
3. `DrainageBasinBoundary.shp` → `subcatchments.geojson` with
   `subcatchment_id` field (no MCP tool).
4. Synthesised `landuse_input.csv` / `soil_input.csv` with placeholder
   `Public` / `loam` values (Saanich `ZoningSHP` cannot be directly used
   as `landuse_class` without a translation table).
5. Hand-authored `options_config.json` after inspecting the example in
   `skills/swmm-builder/`.

Additionally the agent had to:

- Read the `1984rain.dat` file header (`;Rainfall (mm)`) to infer the
  unit was `mm_per_day` — this was not documented in SKILL.md.
- Pick the outfall node from the pipe network by southernmost endpoint
  ("guess").
- Skip the audit step because the orchestration SKILL mandates it as a
  CLI step rather than exposing it via MCP.

## Why this run matters

It is the **first non-operator-assisted** evidence that the framework's
natural-language → autonomous-orchestration claim holds end-to-end.
It also surfaces five gaps that the operator-run cold-start diagnostic
(see `../saanich-smoke-20260513/cold_start_diagnostic.md`) did not catch,
because those gaps are only visible when starting from raw Saanich
shapefiles rather than from a baseline-seeded `00_raw/` directory.

The new gaps are folded into `../BACKLOG.md` (the canonical, living
backlog that supersedes the run-specific backlogs in either run dir).
