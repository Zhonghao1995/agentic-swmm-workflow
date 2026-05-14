---
name: swmm-runner
description: Run EPA SWMM (swmm5) simulations reproducibly and extract key metrics from the report file. Use when an agent needs to (1) run a .inp via swmm5 CLI, (2) generate a run directory with rpt/out + manifest, (3) extract peak flow/time for a node/outfall, (4) parse SWMM continuity (Runoff Quantity / Flow Routing) errors from .rpt, or (5) compare two .rpt files (e.g. GUI vs CLI) for equivalence.
---

# SWMM Runner (CLI-first)

## What this skill provides

- Deterministic execution wrapper around the `swmm5` binary.
- A standard **run directory** layout: `inp/`, `rpt/`, `out/`, `stdout`, `stderr`, `manifest.json`.
- Metric extraction read directly from SWMM's own `.rpt`:
  - peak flow + time-of-peak for any junction or outfall;
  - Runoff Quantity and Flow Routing continuity tables + continuity error %;
  - cross-run comparison (e.g. GUI vs CLI) on continuity error.

## When to use this skill

Use after a SWMM model is fully assembled (typically by `swmm-builder.build_inp`) and you need to actually execute it and read back the metrics. Also use to compare two .rpt files for regression / GUI parity.

Do **not** use this skill to assemble the .inp itself (that's `swmm-builder`) or to plot the results (that's `swmm-plot`).

## MCP tools

`mcp/swmm-runner/server.js` exposes four tools.

1. **`swmm_run`** — run `swmm5` against an .inp and write rpt + out + stdout + stderr + manifest.json into a run directory.
   - Args: `inp` (path), `runDir` (path), `node` (optional), `rptName` (optional), `outName` (optional).
   - When `node` is omitted, the server auto-detects the first entry from the .inp `[OUTFALLS]` section so the manifest's peak metric targets a real outfall name (no more silent "O1" default).
   - Output: a manifest with inp_sha256, swmm5 version, file paths, `metrics.peak`, `metrics.continuity`.

2. **`swmm_peak`** — parse peak flow and time-of-peak for a specific node from a SWMM .rpt. The `node` argument is **required** (no default; the previous misleading "O1" default has been removed).
   - Args: `rpt` (path), `node` (required).
   - Falls back from "Node Inflow Summary" to "Outfall Loading Summary" when no timed inflow entry exists for the node.

3. **`swmm_continuity`** — parse the Runoff Quantity and Flow Routing continuity tables from a SWMM .rpt.
   - Args: `rpt` (path).
   - Returns a structured dict with `Continuity Error (%)` for both blocks plus all the volume rows (precipitation, evaporation, infiltration, runoff, etc.).

4. **`swmm_compare`** — compare continuity-error percentages between two .rpt files (e.g. GUI vs CLI parity check).
   - Args: `rpt`, `rpt2`.

## Recommended orchestration

```
swmm-builder.build_inp      → model.inp
swmm-runner.swmm_run        → model.rpt + manifest.json (with peak + continuity)
swmm-runner.swmm_continuity → structured continuity tables
swmm-runner.swmm_peak       → peak at a specific node (e.g. for downstream plotting)
   ↓
hand off to swmm-plot or swmm-experiment-audit
```

## Conventions

- Read SWMM's own `.rpt` for continuity and peak metrics; do not re-implement physics.
- Keep units explicit in the .inp; SI preferred (`FLOW_UNITS CMS`).
- The .rpt's "Node Inflow Summary" is the authoritative peak source for both junctions and outfalls. If a node has only outfall flow (rare), the parser falls back to "Outfall Loading Summary".

## Known limitations

- `swmm_peak` rounds to .rpt's 3-decimal display precision. Tiny basins (< ~3 ha) routinely produce peaks of order 1e-4 m³/s that show up as `0.000` in the .rpt summary; the .out time-series file has the real precision but is not yet consulted (`BACKLOG.md M3`).
- `swmm_continuity` and `swmm_peak` return structured dicts but do not yet support an `outputPath` to write them as standalone JSON artifacts; the orchestrator currently captures them via the raw MCP response (`BACKLOG.md F10`).
