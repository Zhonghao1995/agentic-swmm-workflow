# Operational Memory

## Default orchestration rule

For a full Agentic SWMM task in a public checkout, load and follow:

```text
skills/swmm-end-to-end/SKILL.md
docs/openclaw-execution-path.md
```

Treat `swmm-end-to-end` as the only top-level SWMM orchestration skill. Treat module skills as subordinate implementation skills.

## Default decision sequence

Before running tools, decide:

1. Is this a full modular build, prepared-input build, minimal Tod Creek fallback, calibration task, uncertainty task, or audit-only task?
2. What concrete files exist?
3. Which critical inputs are missing?
4. What run directory will hold artifacts?
5. Which QA and audit outputs must be produced?

For user-facing modelling sessions, follow `modeling_workflow_memory.md` after these decisions are made. That file defines the complete ordered path from modelling goal through final readiness report.

## Operating modes

### Full modular build

Use when the user has real inputs for:

- subcatchment geometry or builder-ready subcatchments,
- network source or `network.json`,
- rainfall,
- land use,
- soil or accepted pre-merged parameters.

Do not fabricate a network or subcatchments to force this mode.

### Prepared-input build

Use when model-ready artifacts already exist:

- `subcatchments.csv`,
- `network.json`,
- params JSON,
- rainfall JSON, timeseries, or raingage artifacts.

### Minimal Tod Creek fallback

Use when the user wants a real-data smoke test and the full modular path is not ready.

Canonical script:

```bash
python3 scripts/real_cases/run_todcreek_minimal.py
```

This path is useful evidence that real data can run through the repo. It is not proof of a final multi-subcatchment Tod Creek architecture.

### Calibration

Enter calibration only when observed flow exists and the user requested calibration or the workflow explicitly includes it.

### Fuzzy uncertainty

Use `skills/swmm-uncertainty/` when the user asks about epistemic parameter uncertainty, fuzzy membership functions, alpha cuts, or scenario envelopes.

The compact triangular convention is:

- user gives lower and upper bounds,
- the current model value is the triangle peak,
- the current value must lie inside the interval.

## Tool-use rules

- Prefer existing module scripts and MCP tools over one-off code.
- Keep all generated artifacts under `runs/<case>/...`.
- Preserve JSON manifests and summaries.
- Stop on missing critical inputs.
- Run the audit layer after success, failure, or early stop.
- Report the failing stage, missing input, and produced artifacts.
- Do not assume access to the maintainer's private local workspaces or datasets.

## Default output contract

At minimum, a successful build-run-QA workflow should leave:

- SWMM `.inp`,
- SWMM `.rpt`,
- SWMM `.out`,
- `manifest.json`,
- QA summary,
- `experiment_provenance.json`,
- `comparison.json`,
- `experiment_note.md`.
