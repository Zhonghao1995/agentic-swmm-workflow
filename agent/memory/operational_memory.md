# Operational Memory

I try to route by evidence, not by forcing you to choose an internal mode first.

## Mode selection

I start from your goal and available files, infer the mode, then ask only for missing or ambiguous inputs.

- Existing `.inp` file or an example folder with one `.inp`: I use the prepared INP workflow.
- You ask for a demo, smoke test, or onboarding run: I use a prepared demo.
- Raw GIS, rainfall, land use, soil, and network sources are present: I use the full modular build path.
- An existing run directory is provided: I audit, compare, inspect, or plot that run.
- Observed data plus a calibration goal is present: I enter calibration.
- Parameter bounds or fuzzy membership language is present: I use the uncertainty skill.

If the evidence supports more than one path, I'll present two or three concrete choices and recommend the safest default rather than guess silently.

## Prepared INP default

For a trustworthy `.inp`, I follow this sequence:

1. run SWMM,
2. audit the run,
3. inspect available plot options,
4. ask or infer the rainfall series, node or outfall, and node variable,
5. generate the requested plot,
6. summarize artifacts and evidence boundaries.

I won't require a node before the run when a safe outfall or first available node can be discovered. I'll ask for the node and variable before plotting when your plot intent is unclear.

## Interactive session continuity

In an interactive `aiswmm` session, I keep related work under one session folder. I store ordinary dialogue/tool turns separately from SWMM workflow runs, and I keep follow-up actions such as plotting, audit review, or comparison attached to the active run directory.

For later sensitivity or uncertainty workflows, I keep the parent session stable and create multiple child run directories under that session.

## Tool-use rules

- I prefer the unified `agentic-swmm` CLI for run, audit, inspect, and plot.
- I use module skills or MCP tools only when the CLI does not expose the needed stage.
- I keep generated artifacts under `runs/<case>/...`.
- I preserve manifests, command traces, plots, reports, and audit notes.
- I stop on missing critical inputs instead of fabricating rainfall, networks, observed data, or validation evidence.
- I report the selected mode, missing evidence, generated artifacts, and useful next action — and I won't claim *calibrated* or *validated* unless those checks actually ran.
