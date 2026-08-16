---
name: swmm-plot
description: Nature-spec figures from a SWMM run: paired rainfall (inverted) + node/link flow hydrograph (plot_run), network layout map (map_run), study-area map. 89/183 mm columns, 5-7 pt sans-serif, ticks out, no gridlines, Wong colour-blind-safe palette, vector PDF + 450 dpi PNG twin, SI units, no title; optional focus-day / HH:MM window crop. Use whenever an agent needs a publication-ready figure from a run's .inp + .out.
---

# SWMM Plot (Nature figure specification)

Part of [Agentic SWMM](https://github.com/Zhonghao1995/agentic-swmm-workflow): install the project first for the executable toolchain (aiswmm CLI, SWMM solver, MCP servers).

Every figure this skill renders follows the Nature journal figure specification, the
project's standing figure standard. Source:
<https://research-figure-guide.nature.com/figures/preparing-figures-our-specifications/>.
The spec lives in exactly three places here, and the scripts hand-set nothing:

| File | Holds |
|---|---|
| [`assets/nature.mplstyle`](assets/nature.mplstyle) | the stylesheet (fonts, line weights, ticks, palette, TrueType text, constrained layout) |
| [`scripts/plot_style.py`](scripts/plot_style.py) | `apply_style()`, `figsize()`, `map_figsize()`, `save_figure()`, `legend_outside()`, the `WONG` palette |
| this file | the rules a figure must satisfy, and the tool contracts |

**Scope note:** these are print-publication rules for the data figures of a run. They are
not meant for the HTML report chrome or the CLI itself.

## Before calling plot: ask the user

When the user asks to plot, **always ask these questions first** before calling any plot tool:

1. **Which entity?** A specific node (junction / outfall), by name, or a specific link (conduit), by name?
   - List 3-5 high-peak-flow candidates from the run's RPT `Link Flow Summary` so the user can pick.
2. **Which attribute?** Node options: `Total_inflow`, `Depth_above_invert`, `Volume_stored_ponded`, `Flow_lost_flooding`. Link options: `Flow_rate`, `Velocity`, `Depth`.
3. **Time window?** Default is the full simulation (24h). Offer to limit to a focus day or HH:MM-HH:MM window if peaks occur in a short period.

Do **not** silently pick defaults. The user needs control: different plots answer different questions (peak inspection vs continuity vs flooding).

## What this skill renders

| Figure | Script | Reached via | Default output |
|---|---|---|---|
| Rainfall (top, inverted) + node/link flow (bottom) hydrograph | `scripts/plot_rain_runoff_si.py` | `plot_run` tool, `aiswmm plot`, MCP `plot_rain_runoff_si` | `08_plot/fig_<node>_<attr>.png` + `.pdf` |
| Network layout map (subcatchments, conduits, junctions, storage, outfalls; sub-networks coloured per outfall) | `scripts/plot_network_layout.py` | `map_run` tool, `aiswmm map` | `08_plot/network_map.png` + `.pdf` |
| Study-area map (DEM hillshade, subcatchments, conduits, outfalls, scale bar, north arrow, cartouche) | `scripts/plot_study_area.py` | SWMMCanada fetch (`aiswmm canada`), or the script directly | `00_raw/study_area.png` + `.pdf` |

Inputs: rainfall TIMESERIES from the `.inp` (inline, `FILE`, or `[RAINGAGES] FILE`), flow series from the `.out` binary (via `swmmtoolbox`), geometry from the INP text or the SWMManywhere / SWMMCanada upstream layers.

## Non-negotiables

| Rule | Value |
|---|---|
| Width | **89 mm** single column (default). **183 mm** double column only for the hydrograph, via `--width double`, when the series needs the room |
| Max height | **170 mm** (maps take their height from the network's own aspect ratio) |
| Body text | **5 pt min, 7 pt max**, sans-serif (Arial, Helvetica, Nimbus Sans, DejaVu Sans fallbacks) |
| Lines / strokes | **0.25-1 pt** (axes 0.5, hydrograph 0.75, conduits 0.6, polygon edges 0.3, scale bar 1.0) |
| Colour space | **RGB** |
| Palette | Wong colour-blind-safe set (below) |
| Output | vector **PDF** (the submission file) + **PNG at 450 dpi** (preview) with the same stem, written together by `save_figure` |
| Text in the PDF | live, TrueType-embedded (`pdf.fonttype 42`), never outlined |
| Units | SI only; every axis labelled with units in parentheses |
| Title | none; titles belong to the surrounding document |
| Rainfall axis | inverted (depth grows downward) so it never overlaps the flow series |

## Always do

- Start from `apply_style()`; size with `figsize("single" | "double")` or `map_figsize(bounds)`; save with `save_figure(fig, out_png)`. Never `plt.rcParams.update`, never `tight_layout()`, never `bbox_inches="tight"` (it silently changes the physical width and breaks the 89/183 mm rule).
- Axis lines and tick marks on every axis; **ticks point outward**; top/right spines off (the hydrograph's flow axis keeps its own right spine).
- Put the key **above the panel** (`legend_outside`), so it never sits on data.
- Label every axis with units: `Rainfall depth (mm/5 min)`, `Flow (m³/s)`, `Easting (m, EPSG:32610)`, `X (INP units)` when the INP's unit is unknown.
- Keep the figure about the run: the hydrograph draws only the rainfall inside the reported period (a 30-year 5-min climate file is 3M bars otherwise).
- Prefer 89 mm; reach for 183 mm only when the content needs it.

## Never do

- Background gridlines
- Drop shadows, 3D effects, gradients as decoration
- Patterns/hatching to distinguish categories; use solid colours
- Coloured text (series are bound to axes by the key, not by tinted labels)
- Red/green pairings, rainbow/jet colourmaps
- Overlapping text, or text over busy backgrounds (the study-area cartouche sits on a white box)
- A title inside the figure
- A raster-only figure: an orphan PNG is what gets submitted by mistake

## Wong colour-blind-safe palette

Black first, then in this order. Roles in this skill are fixed so the figures of one run read as one set:

| Name | Hex | Role here |
|---|---|---|
| Black | `#000000` | flow line; outfall markers (star, white edge) on both maps; conduits on the study-area map |
| Orange | `#E69F00` | first sub-network colour on the network map |
| Sky blue | `#56B4E9` | rainfall bars; subcatchment fill on the study-area map |
| Bluish green | `#009E73` | sub-network colour |
| Yellow | `#F0E442` | sub-network colour (last: thin yellow lines vanish on white) |
| Blue | `#0072B2` | storage nodes; subcatchment edges on the study-area map |
| Vermillion | `#D55E00` | sub-network colour |
| Reddish purple | `#CC79A7` | sub-network colour |

Continuous data (the DEM hillshade) uses a monotonic grey ramp; never `jet`.

## Workflow

```
inspect_plot_options  ->  real rainfall series name + node/link ids
swmm-runner.run_swmm_inp  ->  model.inp + model.out
plot_run / map_run    ->  08_plot/<name>.png + 08_plot/<name>.pdf
```

Call `inspect_plot_options` first so `plot_run` gets real names instead of placeholders. If several nodes need a figure, call `plot_run` once per node with a different `out_png`; each call writes its own PDF twin.

Writing a new figure type inside this skill? Same three calls:

```python
from plot_style import apply_style, figsize, save_figure, WONG
apply_style()
fig, ax = plt.subplots(figsize=figsize("single"), layout="constrained")
...
save_figure(fig, out_png)        # out.pdf + out.png, size-checked, default bbox
```

### PDF vs PNG: which file is which

The submission file is the **PDF** (vector, live text). The **PNG** is a preview: it is what
`swmm-report` embeds (it globs `08_plot/*.png`), what chat and slides show, what gets pasted
into a draft. Both share a stem, which is how a checker knows the PNG is a preview and not
an orphan raster. `--dpi` only affects the PNG (default 450, the spec's minimum for images).

### Verify before calling a figure done

The repository test `tests/test_swmm_plot_nature_style.py` pins the physical size (89 mm),
TrueType-embedded text and the PDF twin. When the `nature-figures` skill is on the machine,
its checker inspects any output directly:

```bash
python3 ~/.claude/skills/nature-figures/scripts/check_figure.py 08_plot/fig_O1_Total_inflow.pdf 08_plot/fig_O1_Total_inflow.png
```

## MCP tools

This skill backs three LLM-facing tools. `plot_rain_runoff_si` is routed through the MCP server; `inspect_plot_options` and `map_run` are direct Python handlers in the tool registry (`agentic_swmm/agent/tool_handlers/swmm_plot.py` and `swmm_map.py`).

1. **`inspect_plot_options`**: inspect a run directory (or an explicit `.inp` / `.out` path) and return the available rainfall series names, node IDs, and node output attributes. Call this before `plot_run` so you can pass real names instead of placeholders. Required args: `run_dir` (or `inp_path` + `out_file`). Read-only; auto-approved under the QUICK permission profile.

2. **`map_run`**: render the network layout as PNG + PDF. Reads the INP from the run directory automatically; pass `inp` to override. Required arg: `run_dir`. Optional: `out_png`, `dpi`, `no_subcatchments`, `no_vertices`.

3. **`plot_run`** (proxies to `plot_rain_runoff_si` on the MCP server): create the paired rainfall + flow figure from a run directory. Required arg: `run_dir`. Supply either `node` or `link` (mutually exclusive) to select the lower panel. Optional: `rain_ts`, `rain_kind`, `node_attr`, `out_png`. Figures default into the run's canonical plot stage (`08_plot/`), which is where `swmm-report` looks for embeddable figures; a RELATIVE `out_png` is anchored there too (never the process working directory), while an absolute path is honored verbatim. Day-window cropping: pass `focus_day` (`YYYY-MM-DD`) to crop the axis to one calendar day; pass `window_start` and `window_end` (both `HH:MM`) to further narrow to a sub-day window; both require `focus_day` (the server rejects `window_start`/`window_end` without `focus_day`).

**`mcp/swmm-plot/server.js` exposes one underlying tool:**

4. **`plot_rain_runoff_si`**: low-level render call used by `plot_run`. Prefer `plot_run` (which accepts `run_dir`) over calling this directly.
   - Args:
     - `inp` (required): path to the SWMM .inp (the rainfall TIMESERIES is read from here).
     - `out` (required): path to the SWMM .out binary.
     - `outPng` (required): where to write the PNG; the PDF twin lands beside it and is returned as `outPdf`.
     - `rainTs` (no usable default: the schema ships the self-documenting placeholder `<rainfall-series-name>`, which fails at render time if not replaced; always supply the actual series name from the .inp `[TIMESERIES]` section via `inspect_plot_options`): name of the rainfall TIMESERIES inside the .inp.
     - `rainKind` (default `"depth_mm_per_dt"`): one of `intensity_mm_per_hr`, `depth_mm_per_dt`, `cumulative_depth_mm`.
     - `dtMin` (default `5`): timestep of the rainfall series in minutes.
     - `node` (no usable default: the schema ships the self-documenting placeholder `<outfall-or-junction>`, which fails at render time if not replaced; always supply a real outfall or junction name via `inspect_plot_options`): node ID to plot from the .out.
     - `nodeAttr` (default `"Total_inflow"`): which `swmmtoolbox` attribute (e.g. `Total_inflow`, `Lateral_inflow`, `Flow_lost_flooding`).
     - `link` (optional): conduit id; when set, the lower panel plots the link's `Flow_rate` instead of a node attribute. Mutually exclusive with `node`.
     - `width` (default `"single"`): `single` (89 mm) or `double` (183 mm).
     - `dpi` (default `450`): PNG preview resolution; the PDF is vector.
     - `focusDay` (optional, `YYYY-MM-DD`): crop axis to a single day.
     - `windowStart` / `windowEnd` (optional, `HH:MM`; only valid together with `focusDay`): sub-day time window within the focus day. Rejected with a clear error if used without `focusDay`.
     - `padHours` (default `2`): padding around the rainfall extent when no `focusDay` is given.

## Known limitations

- Only one rainfall series is plotted at a time (`rainTs` is a single name); multi-gauge inputs need separate figures.
- Multi-node ensemble plots, exceedance curves and sensitivity scans belong to `swmm-uncertainty` / `swmm-calibration`, not here.
