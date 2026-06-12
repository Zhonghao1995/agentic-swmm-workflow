---
name: swmm-plot
description: Publication-grade plotting for SWMM rainfall–runoff time-series figures. Use when an agent needs to produce a paired rainfall (top, inverted) + node flow (bottom) figure from a SWMM .inp + .out, with strict style rules (SI units, Arial, ticks inward, no title), optionally cropped to an event window or focus day.
---

# SWMM Plot (publication spec)

Part of [Agentic SWMM](https://github.com/Zhonghao1995/agentic-swmm-workflow) — install the project first for the executable toolchain (aiswmm CLI, SWMM solver, MCP servers).

## Before calling plot — ask the user

When the user asks to plot, **always ask these questions first** before calling any plot tool:

1. **Which entity?** A specific node (junction / outfall) — by name — or a specific link (conduit) — by name?
   - List 3–5 high-peak-flow candidates from the run's RPT `Link Flow Summary` so the user can pick.
2. **Which attribute?** Node options: `Total_inflow`, `Depth_above_invert`, `Volume_stored_ponded`, `Flow_lost_flooding`. Link options: `Flow_rate`, `Velocity`, `Depth`.
3. **Time window?** Default is the full simulation (24h). Offer to limit to a focus day or HH:MM-HH:MM window if peaks occur in a short period.

Do **NOT** silently pick defaults. The user needs control — different plots answer different questions (peak inspection vs continuity vs flooding).

## What this skill provides

- A plotting script that reads:
  - rainfall TIMESERIES from a SWMM `.inp` (one named series at a time);
  - flow series from a SWMM `.out` binary (via `swmmtoolbox`).
- Renders a paired rainfall + node-flow figure with a fixed publication style:
  - SI units (rain in mm per timestep or mm/h depending on `rainKind`);
  - rainfall on the top axis, **inverted** (depth grows downward);
  - flow on the bottom axis;
  - inward ticks, Arial, no auto title;
  - optional `focusDay` to crop to a single day; optional `windowStart`/`windowEnd` (**HH:MM**, only together with `focusDay`) for a sub-day window.

## When to use this skill

Use after `swmm-runner.swmm_run` has produced both `.inp` and `.out`, and you want a single rainfall-vs-flow figure for one node (typically the model's outfall or the assigned upstream junction of a subcatchment of interest).

Do **not** use this skill for multi-node ensemble plots, exceedance curves, or sensitivity scans — those would belong to `swmm-uncertainty` / `swmm-calibration` (no plot tools there yet).

## MCP tools

This skill backs three LLM-facing tools. `plot_rain_runoff_si` is routed through the MCP server; `inspect_plot_options` and `map_run` are direct Python handlers in the tool registry (`agentic_swmm/agent/tool_handlers/swmm_plot.py` and `swmm_map.py`).

1. **`inspect_plot_options`** — inspect a run directory (or an explicit `.inp` / `.out` path) and return the available rainfall series names, node IDs, and node output attributes. Call this before `plot_run` so you can pass real names instead of placeholders. Required args: `run_dir` (or `inp_path` + `out_file`). Read-only; auto-approved under the QUICK permission profile.

2. **`map_run`** — render the spatial network layout (subcatchments + conduits + outfalls) as a PNG. Reads the INP from the run directory automatically; pass `inp` to override. Required arg: `run_dir`. Optional: `out_png`, `dpi`, `no_subcatchments`, `no_vertices`.

3. **`plot_run`** (proxies to `plot_rain_runoff_si` on the MCP server) — create a paired rainfall + node-flow figure from a run directory. Required arg: `run_dir`. Supply either `node` or `link` (mutually exclusive) to select the lower panel. Optional: `rain_ts`, `rain_kind`, `node_attr`, `out_png`. Day-window cropping: pass `focus_day` (`YYYY-MM-DD`) to crop the axis to one calendar day; pass `window_start` and `window_end` (both `HH:MM`) to further narrow to a sub-day window — both require `focus_day` (the server rejects `window_start`/`window_end` without `focus_day`).

**`mcp/swmm-plot/server.js` exposes one underlying tool:**

4. **`plot_rain_runoff_si`** — low-level render call used by `plot_run`. Prefer `plot_run` (which accepts `run_dir`) over calling this directly.
   - Args:
     - `inp` (required): path to the SWMM .inp (the rainfall TIMESERIES is read from here).
     - `out` (required): path to the SWMM .out binary.
     - `outPng` (required): where to write the PNG.
     - `rainTs` (no usable default — the schema ships the self-documenting placeholder `<rainfall-series-name>`, which fails at render time if not replaced; always supply the actual series name from the .inp `[TIMESERIES]` section via `inspect_plot_options`): name of the rainfall TIMESERIES inside the .inp.
     - `rainKind` (default `"depth_mm_per_dt"`): one of `intensity_mm_per_hr`, `depth_mm_per_dt`, `cumulative_depth_mm`.
     - `dtMin` (default `5`): timestep of the rainfall series in minutes.
     - `node` (no usable default — the schema ships the self-documenting placeholder `<outfall-or-junction>`, which fails at render time if not replaced; always supply a real outfall or junction name via `inspect_plot_options`): node ID to plot from the .out.
     - `nodeAttr` (default `"Total_inflow"`): which `swmmtoolbox` attribute (e.g. `Total_inflow`, `Lateral_inflow`, `Flow_lost_flooding`).
     - `dpi` (default `300`).
     - `focusDay` (optional, `YYYY-MM-DD`): crop axis to a single day plus padding.
     - `windowStart` / `windowEnd` (optional, `HH:MM`; only valid together with `focusDay`): sub-day time window within the focus day. Rejected with a clear error if used without `focusDay`.
     - `padHours` (default `2`).

## Recommended orchestration

```
inspect_plot_options  →  list available series + node names
swmm-runner.run_swmm_inp  →  model.inp + model.out
plot_run              →  outfall_flow.png  (or junction_flow.png)
```

Call `inspect_plot_options` first to get the real rainfall series name and outfall node name, then pass those to `plot_run`. If multiple nodes need to be plotted, call `plot_run` once per node with a different `out_png` path.

## Conventions

- Strict publication style: do **not** write a title in the plot; titles belong to the surrounding document.
- SI units only.
- Rainfall axis is always inverted; depth grows downward so it doesn't overlap the flow series visually.

## Known limitations

- Only one rainfall series is plotted at a time (`rainTs` is a single name); multi-gauge inputs need separate figures.
