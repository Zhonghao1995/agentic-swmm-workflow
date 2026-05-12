---
name: swmm-end-to-end
description: Standard operating procedure for auditable Agentic SWMM workflows. Use when an agent must decide how to run, QA, plot, audit, summarize, or safely stop an EPA SWMM workflow using the unified agentic-swmm CLI and lower-level module tools only when needed.
---

# SWMM End-to-End Skill

This skill defines the standard operating procedure for using Agentic SWMM in an auditable and reproducible way.

For normal user-facing workflows, prefer the unified `agentic-swmm` CLI. Lower-level scripts and MCP tools may be used only for debugging, development, full modular build stages not yet exposed through the CLI, or specialized functions such as calibration and uncertainty workflows.

The agent should coordinate the workflow, but SWMM execution, QA checks, plotting, audit records, and memory summaries must remain artifact-based and inspectable.

The agent must stop on missing critical inputs instead of inventing model files, drainage networks, rainfall data, observed data, calibration evidence, or validation evidence.

## Primary CLI Workflow

Use this path when the user provides an existing SWMM INP file or chooses a prepared demo.

Prepared INP run:

```bash
agentic-swmm doctor
agentic-swmm run --inp <model.inp> --run-dir runs/<case> --node <node-or-outfall>
agentic-swmm audit --run-dir runs/<case>
agentic-swmm plot --run-dir runs/<case> --node <node-or-outfall>
agentic-swmm memory --runs-dir runs --out-dir memory/modeling-memory
```

Demo run:

```bash
agentic-swmm demo acceptance
agentic-swmm demo tecnopolo
agentic-swmm demo tuflow-raw
```

Use `agentic-swmm audit --compare-to <baseline-run-dir>` when the user asks for a scenario, before/after, or baseline comparison.

By default, `agentic-swmm audit` writes audit artifacts into the run directory and does not export to Obsidian. Use `--obsidian` only when the user explicitly wants the local Obsidian vault updated.

## Standard Run Directory

New CLI prepared-input runs should use:

```text
runs/<case>/
  00_inputs/
  01_runner/
    model.rpt
    model.out
    stdout.txt
    stderr.txt
    manifest.json
  03_plots/
    fig_rain_runoff.png
  manifest.json
  command_trace.json
  experiment_provenance.json
  comparison.json
  experiment_note.md
```

Existing benchmark layouts remain valid evidence and should not be rewritten just to match the new structure. The audit layer can read older stage folders such as `04_builder/`, `05_runner/`, and `06_qa/`.

## Interactive Session Layout

An interactive `aiswmm` shell should keep related work under one parent session folder:

```text
runs/agent/interactive/<session-id>/
  turns/
    001-<question>/
  runs/
    002-<case>/
      00_inputs/
      05_runner/
      07_plots/
      experiment_note.md
```

Follow-up actions such as "plot the previous result", "audit that run again", or "compare this with baseline" should reuse the active run directory instead of starting from a new blank evidence folder. Multi-run workflows such as sensitivity or uncertainty analysis should create multiple child run directories under the same parent session.

The interactive shell should support `/new-session` to start a fresh parent session without exiting the process. Starting a new session clears the active run directory and prevents follow-up plot/audit requests from attaching to the previous workflow.

## Operating Modes

Do not ask the user to pick an internal mode as the first step. Start by identifying the goal and concrete files, infer the safest mode, and ask a targeted question only when the data supports multiple paths or a critical input is missing.

Good first questions are about evidence, not labels:

- "Do you already have a SWMM `.inp` file?"
- "Do you want to run the prepared example or build from raw GIS/rainfall/network files?"
- "For plotting, which rainfall series, node/outfall, and variable do you want?"

### Mode A: Prepared INP CLI workflow

Use this when a trustworthy `.inp` file already exists.

Required inputs:

- SWMM INP file
- target node or outfall for peak-flow parsing and plotting

The INP may be a repository file or a user-provided absolute local path. For an external local path, the CLI must import the file into the run directory first:

```text
runs/<case>/00_inputs/model.inp
```

The original path and SHA256 hash must be recorded in the run manifest. SWMM execution should use the run-local copy, and the audit note should state that this is an external INP import, not a repository demo or validation claim.

Execution:

1. Run `agentic-swmm doctor`.
2. Run `agentic-swmm run`.
3. Run `agentic-swmm audit`.
4. Run `agentic-swmm plot` when the INP contains rainfall timeseries and the OUT file is available.
5. Run `agentic-swmm memory` when the user asks for historical audit summarization or skill-evolution evidence.

### Mode B: Prepared demo workflow

Use this for onboarding, review, CI smoke checks, or website-ready evidence.

Commands:

- `agentic-swmm demo acceptance`
- `agentic-swmm demo tecnopolo`
- `agentic-swmm demo tuflow-raw`

Evidence boundaries:

- `demo acceptance`: environment and core workflow smoke test.
- `demo tecnopolo`: prepared-input SWMM benchmark, not automatic greenfield modeling.
- `demo tuflow-raw`: structured raw GIS-to-INP benchmark, not arbitrary CAD/GIS recognition.

### Mode C: Full modular build

Use this only when explicit GIS, rainfall, parameter, and network inputs exist or can be produced safely by lower-level tools.

Lower-level stage order:

1. `swmm-gis`
2. `swmm-params`
3. `swmm-climate`
4. `swmm-network`
5. `swmm-builder`
6. `agentic-swmm run` or `swmm-runner`
7. QA checks
8. optional `agentic-swmm plot` or `swmm-plot`
9. optional calibration tools
10. `agentic-swmm audit`

Use MCP tools for these lower-level stages when the runtime provides them. Keep every intermediate artifact under `runs/<case>/...`.

### Mode D: Minimal real-data fallback

Use this only when the user wants a real-data smoke test and the full modular path is not ready because trustworthy multi-subcatchment and network inputs are missing.

Script:

```bash
python scripts/real_cases/run_todcreek_minimal.py
agentic-swmm audit --run-dir runs/real-todcreek-minimal --workflow-mode "minimal real-data fallback"
```

This fallback is a smoke test, not a final watershed architecture.

## Stop Rules

The agent must stop and report missing inputs when required evidence is absent.

- No INP file: do not claim SWMM execution.
- No trustworthy network source: do not invent a drainage network for a full modular build.
- No rainfall input: do not create synthetic rainfall unless the user explicitly requests a synthetic test.
- No observed flow data: do not claim calibration success.
- No parsed continuity metrics: do not claim QA passed.
- No plotted OUT series: do not claim hydrograph plotting succeeded.
- No benchmark or audit artifact: do not claim reproducible evidence.

## Evidence Boundary Rules

- Prepared-input benchmarks are not automatic greenfield watershed modeling.
- Raw GIS benchmarks are structured adapters, not arbitrary CAD/GIS recognition.
- Continuity checks come from SWMM report evidence; parser failure must be reported.
- Peak flow must come from `Node Inflow Summary` or the documented outfall fallback, not from `Node Depth Summary`.
- Prior uncertainty smoke tests are not calibrated predictive uncertainty.
- Audit records preserve evidence; they do not prove the model is scientifically correct.
- Modeling memory proposes improvements; accepted skill or workflow changes still require human review and benchmark verification.

## QA Gates

Minimum QA checks for a successful prepared-input run:

- `agentic-swmm doctor` detects Python dependencies and SWMM.
- SWMM return code is zero.
- `.rpt` exists.
- `.out` exists.
- runner `manifest.json` exists.
- continuity metrics can be parsed from the report.
- peak metric can be parsed from the correct source section.
- audit artifacts exist: `experiment_provenance.json`, `comparison.json`, and `experiment_note.md`.

For calibration mode, also require:

- observed flow file exists and parses successfully.
- simulated and observed time periods overlap.
- calibration metrics are written as artifacts.

## Agent Communication Policy

When using this skill, report:

- selected operating mode
- exact commands run
- run directory
- key artifacts generated
- QA status and any missing evidence
- evidence boundary for the result

Do not hide failed stages. A failed run with audit evidence is more useful than an unsupported success claim.

## Lower-Level Tool Use

Use lower-level scripts or MCP tools when the CLI does not yet expose a required function:

- GIS preprocessing
- rainfall formatting
- parameter mapping
- network import and QA
- INP building
- calibration
- uncertainty propagation

After lower-level stages produce a valid INP, return to the CLI for execution and audit where possible:

```bash
agentic-swmm run --inp runs/<case>/<builder-stage>/model.inp --run-dir runs/<case> --node <node>
agentic-swmm audit --run-dir runs/<case>
```

## Public Memory Preload

Before using this skill in Codex, OpenClaw, Hermes, or another compatible runtime, load the Markdown files in `agentic-ai/memory/` when available:

1. `identification_memory.md`
2. `operational_memory.md`
3. `evidence_memory.md`

Those files shape project identity, routing behavior, and evidence boundaries. Load `soul.md`, `modeling_workflow_memory.md`, or `user_bridge_memory.md` only when the task specifically needs deeper product framing, long workflow detail, or user-facing communication guidance. Memory files do not replace CLI execution or artifact checks.
