---
name: swmm-plot
description: Plotting skill for SWMM output artifacts. Use for publication-grade rainfall-runoff figures through the unified CLI when available, and for developing or using lower-level plotting scripts/MCP tools for other SWMM output variables such as node depth, link flow, or link depth.
---

# SWMM Plot Skill

This skill defines how agents should create SWMM figures without overstating what was plotted or fabricating unavailable series.

For the standard rainfall-runoff figure, prefer the unified CLI:

```bash
agentic-swmm plot --run-dir runs/<case> --node <node-or-outfall>
```

The CLI currently wraps:

```text
skills/swmm-plot/scripts/plot_rain_runoff_si.py
```

Lower-level scripts and MCP tools remain valid for debugging, development, or plot types not yet exposed through the CLI.

## Supported Stable Plot

### Rainfall-runoff

Use `agentic-swmm plot` when the run directory contains:

- a SWMM INP file with a rainfall `TIMESERIES` or `RAINGAGES` reference
- a SWMM OUT file
- a target node or outfall with extractable `Total_inflow`

Expected output:

```text
runs/<case>/03_plots/fig_rain_runoff.png
```

Style contract:

- SI units
- rain shown as depth per timestep where applicable
- inverted rainfall axis
- hydrograph in a separate panel
- ticks inward
- Arial-style publication font when available
- no title by default

## Unsupported Or Developing Plot Types

For requests such as:

- node depth
- link flow
- link depth
- conduit flow-depth relationship
- storage depth
- surcharge/flooding time series
- custom comparison plots

do not force the existing rainfall-runoff CLI command to fit the request.

Use this decision order:

1. Confirm that the required `.out` file exists.
2. Confirm the object id exists in the SWMM output.
3. Confirm the requested attribute can be extracted with `swmmtoolbox`.
4. If an existing lower-level script or MCP tool supports the plot, use it.
5. If no script exists, create a narrow script under `skills/swmm-plot/scripts/`, run it, and save the figure as an artifact.
6. Only after the plot type is stable should it be exposed through `agentic-swmm`.

Recommended future CLI shape:

```bash
agentic-swmm plot rain-runoff --run-dir runs/<case> --node OUT_0
agentic-swmm plot node-depth --run-dir runs/<case> --node J1
agentic-swmm plot link-flow --run-dir runs/<case> --link C11
agentic-swmm plot link-depth --run-dir runs/<case> --link C11
agentic-swmm plot timeseries --run-dir runs/<case> --object-type node --object-id J1 --attribute Depth
```

The current CLI exposes only the rainfall-runoff behavior. Treat the future commands above as a design target, not current evidence.

## Evidence Rules

- Do not claim a plot was generated unless the image file exists.
- Do not claim a variable was plotted unless it was extracted from the SWMM OUT file or another explicit artifact.
- Do not infer calibration quality from a plot alone.
- Do not use `Node Depth Summary` as a source for flow.
- If the requested object id or attribute is missing, stop and report the missing evidence.
- If rainfall data cannot be found in the INP, report that rainfall-runoff plotting is unavailable instead of inventing rainfall.

## MCP Role

The plot MCP server is a fine-grained agent tool interface. It should expose stable plotting functions to agent runtimes. The current MCP/tooling may call lower-level scripts directly.

The CLI and MCP should share the same underlying plotting scripts where possible:

```text
agentic-swmm plot -> skills/swmm-plot/scripts/...
swmm-plot MCP     -> skills/swmm-plot/scripts/...
```

Avoid duplicating plotting logic separately in CLI and MCP.

## Development Rule

When adding a new plot type:

1. Implement or update a focused script in `skills/swmm-plot/scripts/`.
2. Verify it against a real `.out` artifact.
3. Save the output under the run directory, preferably `03_plots/`.
4. Add or update a focused test when practical.
5. Expose it through MCP if agent runtimes need fine-grained access.
6. Expose it through CLI only after the interface is stable enough for users, CI, and README examples.
