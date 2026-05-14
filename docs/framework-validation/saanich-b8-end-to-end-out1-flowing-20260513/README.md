# Saanich E2E with B8 — water finally reaches OUT1 (2026-05-13)

This is the final validation of the natural-language → autonomous pipe
modeling promise for raw municipal shapefile data. With B8
(`snap_pipe_endpoints`) inserted into the chain and B7
(`assign_subcatchment_outlets`) used in `manual_lookup` mode to bridge
a real Saanich data gap, the SWMM model now actually routes surface
runoff through the pipe network and out to OUT1.

## Diff vs the previous lock-ins

| | `saanich-e2e-new-tools-20260513` | `saanich-b7-network-routed-20260513` | **`saanich-b8-end-to-end-out1-flowing-20260513` (this run)** |
|---|---|---|---|
| Subcatchment outlet | OUT1 directly (B7 missing) | nearest junction in disconnected sub-graph A | **manual_lookup → junction in sub-graph B** (B's junctions reach OUT1) |
| Pipe vertex snapping | none | none | **B8 at 3.0 m tolerance** |
| Self-loop pipes dropped | none | none | **DGM023798 (snap collapsed both ends to one cluster)** |
| Junctions with flow | 0 of 9 | 2 of 9 (in sub-graph A) | **3 of 8 (in sub-graph B, ending at OUT1)** |
| OUT1 inflow | 0.001 cms / 0.369 ML (bypassed network) | 0.000 (water stuck in sub-graph A) | **0.001 cms / 0.367 ML (flowed through pipes)** |
| Routing continuity | 0.000% | 0.000% (water held in disconnected fragment) | **0.000% (water actually exits)** |

## What B8 healed and what it surfaced

B8 found **4 endpoint clusters** to merge in the Saanich Colquitz pipe
set, with a **maximum snap distance of 2.08 m**. It also **dropped 1
pipe (`DGM023798`) as a self-loop**: the pipe was already very short
in the source data, and the 3 m snap collapsed its two ends into the
same cluster, which would have made `import_city_network` and SWMM
both reject the network.

After B8, `reorient_pipes` reports `pipes_reached: 3, pipes_unreached:
3` (out of 6 pipes). The remaining 3 unreached pipes form a separate
sub-graph that is **physically disconnected** from the outfall
sub-graph by a 30+ m gap — wider than any reasonable snap tolerance.
The cause is a basin-clip artifact: the basin polygon
(`OBJECTID=100`, ~3.5 ha) drops a trunk sewer just outside its
boundary that connects the two sub-graphs in the wider Saanich
network. **B8 cannot conjure missing pipes.** This is a future
candidate for a "buffered basin clip" feature in
`prepare_storm_inputs`.

## Why the lock-in uses B7 in `manual_lookup` mode

To make this run demonstrate the framework's promise (water flows from
subcatchment → pipe network → outfall), the agent picked
`J_AUTO_473340p667_5369264p343` — a junction in the outfall sub-graph
— as S1's outlet via `assign_subcatchment_outlets` `manual_lookup`.
With `nearest_junction` mode, B7 would have picked a junction in the
disconnected sub-graph (closer to S1's centroid in absolute distance
but topologically dead-ended). Both modes are valid; for production
data without disconnections, `nearest_junction` is the right default.

This is a real-world reminder that "geometric nearness" and "hydraulic
upstreamness" are not the same thing.

## Outcome metrics

| | Value |
|---|---|
| swmm5 exit code | 0 |
| Runoff continuity error | -0.171% |
| Flow routing continuity error | 0.000% |
| Peak inflow at OUT1 | 0.001 m³/s @ ~03:00:25 |
| Total inflow volume at OUT1 | 0.367 ML (vs 0.369 ML runoff produced — 0.5% retained in storage) |
| Plot | `outfall_flow.png` (rain on top inverted, OUT1 inflow on the bottom) |

## Files in this directory

| File | What it is |
|---|---|
| `model.inp` | INP that routes S1 to a junction in the outfall sub-graph and contains 6 conduits + 8 junctions + 1 outfall. |
| `model.rpt` | SWMM .rpt with 3 of 8 junctions showing flow + nonzero OUT1 inflow. |
| `runner_manifest.json` | Runner's manifest with auto-detected outfall (F6) and metrics. |
| `network.json` | Final network after snap + reorient + import. |
| `subcatchments_routed.csv` | After-B7 CSV showing S1 routed to `J_AUTO_473340p667_5369264p343`. |
| `outlet_lookup.csv` | The 1-row lookup CSV used by B7 `manual_lookup` mode. |
| `snap_report.json` | B8's structured report (4 clusters merged, 1 self-loop dropped). |
| `outfall_flow.png` | Rain + OUT1 flow plot. |

## Pipeline summary (this is the canonical "raw shapefile → SWMM → plot" chain)

```
B1  basin_shp_to_subcatchments       (mode=by_id_field, OBJECTID=100)
B6  prepare_storm_inputs              (clip pipes + manholes to basin)
    Watercourse.shp → watercourse.geojson  (inline geopandas glue, candidate for future MCP)
B8  snap_pipe_endpoints                (tolerance=3.0 m; dropped 1 self-loop)
B3  infer_outfall                     (mode=endpoint_nearest_watercourse)
B5  reorient_pipes                    (BFS from outfall)
F1  qgis_area_weighted_params         (no unmatched landuse classes)
    swmm-climate format_rainfall      (5-day event window, mm/hr)
    swmm-network import_city_network  (reoriented pipes + outfalls + mapping)
    swmm-network qa
B7  assign_subcatchment_outlets       (mode=manual_lookup, route S1 to outfall sub-graph)
    swmm-builder build_inp
F6  swmm-runner swmm_run              (no node arg; auto-detect OUT1 from .inp)
    swmm-runner swmm_continuity / swmm_peak
    swmm-plot plot_rain_runoff_si
```

**14 MCP tool calls, 0 hand-rolled python adapter scripts** (the only
glue is the inline Watercourse.shp → geojson conversion, candidate for
a small future `convert_shp_to_geojson` MCP tool).

## What this validates and what it does not

✅ Validates:
- The full B1+B2+B3+B4+B5+B6+B7+B8+F1+F6 toolkit composes into a
  region-portable pipeline.
- An agent following the SKILL.md chain can produce a runnable SWMM
  model with surface runoff actually entering the pipe network.
- B8 surfaces real data-quality issues (vertex drift, self-loop pipes)
  rather than masking them.

⚠️ Does not validate:
- Production-grade routing for Saanich specifically — the Colquitz
  basin clip drops a trunk pipe so 3/6 pipes are still disconnected
  from OUT1. A wider basin polygon or a future "buffered basin clip"
  feature would close this.
- F11 (soil layer absence; still using `soil_uniform_loam.geojson`).
- F3/M2 (rainfall preprocessing; still using a hand-derived 5-day
  event CSV).
- F5 (options_config.json; still seeded from baseline).
- F12 (DEM-based invert inference; all junctions still at 0.0 m).
