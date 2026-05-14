# Saanich MCP-first framework smoke — 2026-05-13 lock-in

This directory is a permanent (git-tracked) copy of the key artifacts from the
re-run of the Saanich end-to-end smoke after the `mcp/` top-level restructure.
The corresponding ephemeral run dir at
`runs/20260513-030729-saanich-framework-smoke/` is `.gitignored`; if you need
the full artefact tree (mcp_transport responses, intermediate stages, raw
SWMM .rpt / .out, etc.) regenerate by re-running Mode 0 of `swmm-end-to-end`.

## What this run proves

- 8/8 MCP tool calls of Mode 0 returned `status: ok` after the restructure
  (qgis_area_weighted_params → format_rainfall → import_city_network → qa →
  build_inp → swmm_run → swmm_continuity → swmm_peak).
- `model.inp` produced by `swmm-builder-mcp.build_inp` is byte-identical to the
  pre-restructure baseline at `runs/20260513-014020-saanich-framework-smoke`.
- SWMM 5.2.4 ran the .inp end-to-end (`return_code = 0`), peak inflow at OUT1
  = 0.001 cms at 21:45, runoff continuity error = -0.109%, flow routing
  continuity error = 0.0% — all matching baseline.
- Natural-language trigger ("用 Saanich 数据测试…") successfully routed to
  `swmm-end-to-end` and the agent autonomously orchestrated all 8 calls via
  `mcp_stdio_call.py`.

## Files

| File | What it is |
|---|---|
| `framework_mcp_manifest.json` | Ordered record of the 8 MCP tool calls with response/output paths, fallback inputs, and framework gaps. |
| `framework_smoke_summary.json` | Top-level metrics (peak, continuity, return code, artefact pointers). |
| `model.inp` | The SWMM input deck produced end-to-end (the "组装 inp" output). |
| `cold_start_diagnostic.md` | Step-by-step narrative of every implicit-knowledge / undocumented prerequisite the operator had to supply. |
| `cold_start_diagnostic.json` | Structured form of the same — 15 entries, severity tagged (blocking / friction / minor). |
| `framework_gaps_backlog.md` | Consolidated, prioritised to-do list for the (B) quality-improvement phase. |

## Status

- (A) phase: **complete**. Framework transport health verified.
- (B) phase: **starting** — see `framework_gaps_backlog.md` for the ordered
  list of MCP / skill improvements derived from this run.
