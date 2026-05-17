# Saanich E2E with B7 — pipe network actually used (2026-05-13)

This is the validation lock-in for **B7
(`assign_subcatchment_outlets`)**, the new MCP tool that rewrites
each subcatchment's `outlet` column from the literal outfall to a
real upstream junction so the pipe network actually carries surface
water in the SWMM model.

> **Frozen evidence note.** Paths in `network.json` and
> `runner_manifest.json` are **absolute to the maintainer's checkout at
> capture time (2026-05-13)**. They are preserved as captured to keep
> the evidence trail honest. To re-run on a different machine,
> regenerate the artifact via `scripts/benchmarks/...` and inspect the
> new repo-relative paths in the freshly produced JSON.

## What B7 changed

Diff of `subcatchments.csv` before vs after the B7 step:

```
BEFORE                                                 AFTER
S1,OUT1,3.522051,...                                   S1,J_AUTO_473439p647_5369218p478,3.522051,...
```

S1's centroid is 23.7 m from junction
`J_AUTO_473439p647_5369218p478`. B7's `nearest_junction` mode picked
that node as the upstream entry point.

## Pipe network is now exercised — comparison

Before B7 (E2E lock-in `saanich-e2e-new-tools-20260513/`), the
.rpt's Node Inflow Summary read:

```
J_AUTO_*  ... 9 junctions all 0.000 lateral / 0.000 total / 0 ltr volume
OUT1      OUTFALL 0.001 cms @ 17:50, 0.369 ML  ← runoff bypassed network
```

After B7 (this run):

```
J_AUTO_473439p647_5369218p478 JUNCTION 0.001 cms @ 17:50, 0.369 ML  ← assigned outlet
J_AUTO_473448p553_5369219p260 JUNCTION 0.000 cms @ 17:25, 0.369 ML  ← propagated downstream
... 7 other junctions: still 0.000 ...
OUT1                          OUTFALL  0.000 cms                    ← see B8 below
```

**Result:** runoff now enters the pipe network at a real upstream
junction and propagates one pipe downstream. This is a strictly more
faithful model than "subcatchment dumps directly to outfall".

## Why OUT1 reads 0 — discovery of B8

Saanich's `StormGravityMain.shp` clipped to the Colquitz basin
contains 7 pipe segments, but only **3 of them are graph-connected**
via shared vertices (B5's `pipes_reached: 3`). The other **4 pipes
are disconnected fragments** (their endpoints don't share vertices
within 3-decimal precision). Before B7, this fragmentation was
invisible because the subcatchment routed water around the network
entirely. With B7, the runoff enters the connected sub-network and
stalls at the disconnected boundary — `routing continuity = 0.000%`,
`Final Stored Volume` shows the water held in the connected fragment.

This exposes a new framework gap that the previous workflow masked:
**B8 — pipe vertex snapping / network healing.** Filed as new entry
in `../BACKLOG.md`. B8 is independent of B7 and orthogonal to the
"natural-language → autonomous pipe modeling" promise: B7 ships the
correct routing semantics; B8 is needed for Saanich-quality
real-world data hygiene.

## Outcome metrics

| | Value |
|---|---|
| swmm5 exit code | 0 |
| Runoff continuity error | -0.171% (unchanged from pre-B7 run) |
| Flow routing continuity error | 0.000% (water held in connected fragment) |
| Peak inflow at assigned junction | 0.001 m³/s @ 17:50 |
| Peak inflow at OUT1 | 0.000 (Saanich pipe fragmentation; see B8) |
| Plot file | `junction_flow.png` (rain + flow at the assigned junction) |

## Files in this directory

| File | What it is |
|---|---|
| `model.inp` | INP whose [SUBCATCHMENTS] row routes S1 to a junction (not OUT1). |
| `model.rpt` | SWMM .rpt showing junction-level inflow rather than outfall-only inflow. |
| `runner_manifest.json` | Manifest with peak metric. |
| `network.json` | Same network as the pre-B7 lock-in (B7 doesn't touch network.json). |
| `subcatchments_before_b7.csv` | Output of `basin_shp_to_subcatchments` — outlet=OUT1. |
| `subcatchments_after_b7.csv` | Output of `assign_subcatchment_outlets` — outlet=J_AUTO_*. |
| `junction_flow.png` | Plot of rain + total inflow at the assigned junction. |

## Where B7 sits in the pipeline

```
B1 basin_shp_to_subcatchments
    → subcatchments.geojson + subcatchments.csv (outlet=OUT1 placeholder)
B6 prepare_storm_inputs
    → pipes.geojson + manholes.geojson + mapping.json
B3 infer_outfall
    → outfalls.geojson
B5 reorient_pipes
    → pipes_oriented.geojson
qgis_area_weighted_params (F1 lookup)
    → weighted_params.json
format_rainfall
    → rainfall.json + timeseries.txt
import_city_network
    → network.json
qa
    → ok
B7 assign_subcatchment_outlets  ← NEW STEP
    → subcatchments_routed.csv (outlet rewritten to upstream junction)
build_inp                       ← takes the ROUTED csv
swmm_run (F6 auto-detect)
swmm_continuity / swmm_peak / plot_rain_runoff_si
```

Total: **13-step MCP chain** (was 12, B7 is the new step 9.5).
