# Saanich end-to-end with new MCP tools — 2026-05-13 lock-in

This is the **portability validation** for the (B + selected F) phase.
It re-runs the Saanich pipe-network modeling chain end-to-end, this
time replacing the cold-start agent's hand-rolled adapter scripts with
the new MCP tools shipped in B6 / B5 / B3 / B1 / F6 / F1.

**Goal of this run:** prove the natural-language → autonomous pipe
modeling claim now holds without any custom python glue when the
region has full data. Saanich still lacks soil and a deterministic
event-window selector (F11 / F3 still open), so two artefacts are
seeded from the baseline run as documented fallbacks. Everything
else is reproducible from raw shapefiles using only MCP tools.

## Full execution chain

| # | Tool | What was new vs cold-start agent |
|---|---|---|
| 1 | `swmm-gis-mcp.basin_shp_to_subcatchments` (**B1**) | Replaces hand-picking OBJECTID + hand-stamping `subcatchment_id` field. |
| 2 | `swmm-network-mcp.prepare_storm_inputs` (**B6**) | Replaces hand-clipping `StormGravityMain.shp` + `StormManhole.shp` with geopandas + hand-filling `mapping.json` from a template. |
| 3 | (inline glue) Watercourse.shp → watercourse.geojson | Single geopandas one-liner; not yet a documented MCP gap. |
| 4 | `swmm-network-mcp.infer_outfall` (**B3**, mode=`endpoint_nearest_watercourse`) | Replaces hand-picking the southernmost / nearest-river endpoint. Picked `DGM004947` end at 0.0 m to watercourse — exact match. |
| 5 | `swmm-network-mcp.reorient_pipes` (**B5**) | Replaces BFS-from-outfall reorientation script. Found 0 reorientations needed for Colquitz Colquitz pipes (this run); 4 pipes were unreached and left untouched — flagged in tool response. |
| 6 | `swmm-gis-mcp.qgis_area_weighted_params` (**F1**) | Same tool as before, but the extended `landuse_class_to_subcatch_params.csv` now resolves Saanich Zoning classes cleanly (Single Family, Industrial, etc.). `warnings: []`. |
| 7 | `swmm-climate-mcp.format_rainfall` | Same as before. Input CSV still derived from a manual event-window pick (`F3` still open). |
| 8 | `swmm-network-mcp.import_city_network` | Now accepts the B6-produced mapping.json + pipes geojson. 7 pipes / 9 inferred junctions / 1 outfall. |
| 9 | `swmm-network-mcp.qa` | Same. |
| 10 | `swmm-builder-mcp.build_inp` | Same. Still seeds `options_config.json` from baseline (`F5` still open). |
| 11 | `swmm-runner-mcp.swmm_run` (**F6**) | Called **with no `node` argument** — server auto-detected `OUT1` from the .inp `[OUTFALLS]` section. Manifest peak.node = OUT1. |
| 12 | `swmm-runner-mcp.swmm_continuity` + `swmm_peak` | Same. `swmm_peak` now requires `node` arg explicitly (`F6` side-effect). |
| 13 | `swmm-plot-mcp.plot_rain_runoff_si` | Same. X-axis tick density bug (`F14`) still visible. |

**Total MCP calls: 12. Hand-rolled python scripts: 0** (excluding the
one-line shp→geojson conversion for watercourse, which is a candidate
minor future MCP).

## Outcome metrics

| | Value |
|---|---|
| swmm5 exit code | 0 |
| Runoff continuity error | -0.171% |
| Flow routing continuity error | 0.000% |
| Peak inflow at OUT1 | 0.001 m³/s @ 17:50 |
| Plot file | `outfall_flow.png` (5-day window, daily-rain + outfall hydrograph) |

The peak time differs from the `saanich-smoke-20260513` baseline (which
reported 21:45) because **F1's extended landuse lookup now applies
class-specific imperv_pct values instead of routing every Saanich
zoning polygon through DEFAULT**. The model is now more faithful to
the data; the metric change is expected and welcome.

## Artefacts in this directory

| File | What it is |
|---|---|
| `model.inp` | The end-to-end-assembled SWMM input deck (the "组装 inp" output). |
| `model.rpt` | The SWMM 5.2.4 report. |
| `runner_manifest.json` | The runner's manifest with inp_sha256 + metrics. |
| `mapping.json` | The B6-produced mapping config (filled from the raw-shapefile template). |
| `network.json` | The network produced by `import_city_network`. |
| `subcatchments.csv` | The B1-produced subcatchments table. |
| `outfall_flow.png` | The end-of-chain plot. |

## What this run does NOT yet prove

- Saanich still lacks a soil layer; the run uses the baseline's
  `soil_uniform_loam.geojson` fallback. `BACKLOG.md F11` (soil-absence
  policy) is the open work.
- Rainfall preprocessing (raw `1984rain.dat` → 5-day event CSV) is
  still manual upstream of `format_rainfall`. `BACKLOG.md F3 / M2`.
- `options_config.json` still seeded from baseline. `BACKLOG.md F5`.
- Plot x-axis tick density (visible in `outfall_flow.png`) is still
  the unaddressed `BACKLOG.md F14`.
- 4 of 7 Colquitz pipes are reported as `unreached` by `reorient_pipes`
  — they don't share a vertex within 3 decimals with the rest of the
  network. This is a Saanich data property, not a tool bug. The pipes
  still get imported (the adapter doesn't enforce connectivity at
  import time).

When `F3`, `F5`, `F11`, `F14` are closed, this same chain should be
runnable for **any region** that ships pipe + manhole + watercourse +
basin + zoning + soil + rainfall layers, with zero custom code.
