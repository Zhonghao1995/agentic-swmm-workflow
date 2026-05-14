---
name: swmm-plot
description: Publication-grade plotting for SWMM rainfall–runoff time-series figures. Use when an agent needs to produce a paired rainfall (top, inverted) + node flow (bottom) figure from a SWMM .inp + .out, with strict style rules (SI units, Arial, ticks inward, no title), optionally cropped to an event window or focus day.
---

# SWMM Plot (publication spec)

## What this skill provides

- A plotting script that reads:
  - rainfall TIMESERIES from a SWMM `.inp` (one named series at a time);
  - flow series from a SWMM `.out` binary (via `swmmtoolbox`).
- Renders a paired rainfall + node-flow figure with a fixed publication style:
  - SI units (rain in mm per timestep or mm/h depending on `rainKind`);
  - rainfall on the top axis, **inverted** (depth grows downward);
  - flow on the bottom axis;
  - inward ticks, Arial, no auto title;
  - optional `windowStart`/`windowEnd` or `focusDay` to crop the time axis.

## When to use this skill

Use after `swmm-runner.swmm_run` has produced both `.inp` and `.out`, and you want a single rainfall-vs-flow figure for one node (typically the model's outfall or the assigned upstream junction of a subcatchment of interest).

Do **not** use this skill for multi-node ensemble plots, exceedance curves, or sensitivity scans — those would belong to `swmm-uncertainty` / `swmm-calibration` (no plot tools there yet).

## MCP tools

`mcp/swmm-plot/server.js` exposes one tool.

1. **`plot_rain_runoff_si`** — render the paired figure to PNG.
   - Args:
     - `inp` (required): path to the SWMM .inp (the rainfall TIMESERIES is read from here).
     - `out` (required): path to the SWMM .out binary.
     - `outPng` (required): where to write the PNG.
     - `rainTs` (default `"TS_RAIN"`): name of the rainfall TIMESERIES inside the .inp.
     - `rainKind` (default `"depth_mm_per_dt"`): one of `intensity_mm_per_hr`, `depth_mm_per_dt`, `cumulative_depth_mm`.
     - `dtMin` (default `5`): timestep of the rainfall series in minutes.
     - `node` (default `"O1"`): node ID to plot from the .out.
     - `nodeAttr` (default `"Total_inflow"`): which `swmmtoolbox` attribute (e.g. `Total_inflow`, `Lateral_inflow`, `Flow_lost_flooding`).
     - `dpi` (default `300`).
     - `focusDay` (optional, `YYYY-MM-DD`): crop axis to a single day plus padding.
     - `windowStart` / `windowEnd` (optional ISO timestamps): explicit time window.
     - `padHours` (default `2`).

## Recommended orchestration

```
swmm-runner.swmm_run            → model.inp + model.out
swmm-plot.plot_rain_runoff_si   → outfall_flow.png  (or junction_flow.png)
```

If multiple basins / nodes need to be plotted, call `plot_rain_runoff_si` once per node with a different `outPng` path.

## Conventions

- Strict publication style: do **not** write a title in the plot; titles belong to the surrounding document.
- SI units only.
- Rainfall axis is always inverted; depth grows downward so it doesn't overlap the flow series visually.

## Known limitations

- For long simulations (many days), the x-axis tick labels overlap because the plotter writes one tick per timestep. `BACKLOG.md F14` covers a future fix using `matplotlib.dates.AutoDateLocator`.
- Only one rainfall series is plotted at a time (`rainTs` is a single name); multi-gauge inputs need separate figures.
