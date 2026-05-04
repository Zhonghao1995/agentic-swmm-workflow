# Modeling Workflow Memory

## Purpose

This memory tells an agent how to guide a public repository user through a complete Agentic SWMM modelling and verification workflow in order.

Use it after `operational_memory.md` and before `evidence_memory.md`.

## User-facing workflow order

When a user asks to build or verify a SWMM model, guide the session in this order:

1. Define the modelling goal.
2. Inventory available input files.
3. Select the workflow mode.
4. Create a run directory.
5. Prepare or validate stage inputs.
6. Build the SWMM input file.
7. Run SWMM.
8. Run QA checks.
9. Create plots if requested or useful.
10. Run calibration only if observed data and intent exist.
11. Run fuzzy uncertainty only if uncertainty bounds or membership functions exist.
12. Run experiment audit.
13. Report readiness and evidence boundaries.

Do not skip directly to SWMM execution unless the required prepared inputs already exist.

## Step 1: Define the modelling goal

Ask or infer the narrow modelling goal:

- build only,
- build + run,
- build + run + QA,
- prepared-input benchmark,
- real-data smoke test,
- calibration,
- uncertainty propagation,
- audit or comparison of an existing run.

Default to build + run + QA when the user says they want a complete modelling workflow.

## Step 2: Inventory available input files

Before selecting a path, check for these input classes:

- subcatchment geometry or `subcatchments.csv`,
- network source files or `network.json`,
- rainfall input,
- land use input,
- soil input,
- existing SWMM `.inp`,
- observed flow file for calibration,
- fuzzy parameter space for uncertainty.

Report missing inputs by class and expected format. Do not ask for "all data" generically.

## Step 3: Select the workflow mode

Use this decision tree:

- If a complete external `.inp` already exists, use prepared-input run/QA/audit.
- If `subcatchments.csv`, `network.json`, params, and rainfall artifacts exist, use prepared-input build.
- If raw GIS, network, rainfall, land use, and soil inputs exist, use full modular build.
- If public examples are the only available data, use an example or benchmark path.
- If the user wants real-data execution and Tod Creek fallback inputs exist, use the minimal Tod Creek fallback.
- If only existing run artifacts exist, use audit-only or comparison mode.

Explain the selected mode in one sentence before running tools.

## Step 4: Create a run directory

Use a stable run directory:

```text
runs/<case>/
```

If the user did not provide a case name, choose a short, filesystem-safe name from the scenario or input source.

Keep all generated stage outputs inside this directory.

## Step 5: Prepare or validate stage inputs

For full modular builds, follow the stage order:

1. GIS or subcatchment preprocessing.
2. Land use and soil parameter mapping.
3. Rainfall formatting and raingage section generation.
4. Network import or network QA.

Stop if a critical input is missing. A full modular build requires a real network source or `network.json`; do not invent one.

## Step 6: Build the SWMM input file

Use `swmm-builder` to create:

```text
runs/<case>/05_builder/model.inp
runs/<case>/05_builder/manifest.json
```

Treat builder validation failures as hard stops unless the user explicitly asks for a diagnostic-only artifact.

## Step 7: Run SWMM

Use `swmm-runner` to execute the built or provided `.inp`.

Expected outputs:

```text
runs/<case>/06_runner/model.rpt
runs/<case>/06_runner/model.out
runs/<case>/06_runner/manifest.json
```

Do not treat the run as successful until the return code and output artifacts are checked.

## Step 8: Run QA checks

Minimum QA checks:

- continuity,
- mass balance when available,
- peak flow parsed from the correct report section,
- output artifact existence,
- manifest completeness.

Expected outputs:

```text
runs/<case>/07_qa/continuity.json
runs/<case>/07_qa/peak.json
```

If QA fails, report the failed check and continue to audit the partial run.

## Step 9: Create plots

Create rainfall-runoff plots when the user asks for visualization, reporting, or paper-facing artifacts.

Keep plots in:

```text
runs/<case>/08_plot/
```

## Step 10: Calibration gate

Run calibration only when:

- the user requested calibration or model fitting,
- an observed flow file exists,
- the observed series parses,
- the observed and simulated periods overlap.

If these conditions are not met, explain what is missing and continue with run/QA/audit.

## Step 11: Fuzzy uncertainty gate

Run fuzzy uncertainty only when:

- the user asks for uncertainty propagation, or
- a fuzzy/interval parameter space is provided, or
- the workflow explicitly includes scenario envelopes.

Use `skills/swmm-uncertainty/` and keep uncertainty artifacts in:

```text
runs/<case>/09_uncertainty/
```

## Step 12: Experiment audit

Always run the audit layer after success, failure, or early stop.

Expected outputs:

```text
runs/<case>/experiment_provenance.json
runs/<case>/comparison.json
runs/<case>/experiment_note.md
```

The audit record should preserve partial evidence. It should not pretend missing stages were completed.

## Step 13: Final readiness report

End with a concise readiness report:

- selected workflow mode,
- run directory,
- completed stages,
- produced artifacts,
- QA results,
- audit files,
- missing inputs or failed checks,
- whether the result is runnable, checked, audited, calibrated, validated, or only a smoke test.

Use precise language. A completed SWMM run is not automatically a calibrated or validated model.
