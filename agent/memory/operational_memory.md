# Operational Memory

aiswmm should route by evidence, not by forcing the user to choose an internal mode first.

## Mode selection

Start from the user's goal and available files. Infer the mode, then ask only for missing or ambiguous inputs.

- Existing `.inp` file or an example folder with one `.inp`: use the prepared INP workflow.
- User asks for a demo, smoke test, or onboarding run: use a prepared demo.
- Raw GIS, rainfall, land use, soil, and network sources are present: use the full modular build path.
- Existing run directory is provided: audit, compare, inspect, or plot that run.
- Observed data plus a calibration goal is present: enter calibration.
- Parameter bounds or fuzzy membership language is present: use the uncertainty skill.

If the evidence supports more than one path, present two or three concrete choices and recommend the safest default.

## Prepared INP default

For a trustworthy `.inp`, use this sequence:

1. run SWMM,
2. audit the run,
3. inspect available plot options,
4. ask or infer the rainfall series, node or outfall, and node variable,
5. generate the requested plot,
6. summarize artifacts and evidence boundaries.

Do not require a node before the run when a safe outfall or first available node can be discovered. Ask for the node and variable before plotting when the user's plot intent is unclear.

## Interactive session continuity

In an interactive `aiswmm` session, keep related work under one session folder. Store ordinary dialogue/tool turns separately from SWMM workflow runs, and keep follow-up actions such as plotting, audit review, or comparison attached to the active run directory.

For later sensitivity or uncertainty workflows, keep the parent session stable and create multiple child run directories under that session.

## Tool-use rules

- Prefer the unified `agentic-swmm` CLI for run, audit, inspect, and plot.
- Use module skills or MCP tools only when the CLI does not expose the needed stage.
- Keep generated artifacts under `runs/<case>/...`.
- Preserve manifests, command traces, plots, reports, and audit notes.
- Stop on missing critical inputs instead of fabricating rainfall, networks, observed data, or validation evidence.
- Report the selected mode, missing evidence, generated artifacts, and useful next action.
