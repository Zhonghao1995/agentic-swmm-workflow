---
name: swmm-experiment-audit
description: Consolidate Agentic SWMM run artifacts into auditable provenance, comparison records, and Obsidian-compatible experiment notes. Use after any SWMM build/run/QA attempt, successful or failed, when OpenClaw or a CLI workflow needs a traceable record of inputs, commands, artifacts, metrics, QA checks, and run-to-run differences.
---

# SWMM Experiment Audit

## What this skill provides

- A standard audit layer for Agentic SWMM runs.
- Consolidation of dispersed `manifest.json`, QA JSON, logs, metrics, and artifact paths.
- Machine-readable outputs for reproducibility and review.
- Obsidian-compatible Markdown notes for human research records.
- Optional run-to-run comparison for baseline/scenario or before/after parser validation.

This skill records what happened. It does not run SWMM, build models, invent missing artifacts, or replace module-level validation.

## When to use this skill

Use this skill after any of these events:

- `swmm-end-to-end` completes successfully.
- `swmm-end-to-end` stops or fails after producing partial artifacts.
- A user wants an Obsidian-ready experiment note for an existing run directory.
- A user wants to compare two run directories.
- A run needs evidence for reproducibility, metric provenance, QA status, or paper claims.

Do not use this skill as a substitute for `swmm-runner`, `swmm-builder`, or calibration tools. Run the model first, then audit the run directory.

## Output contract

For every audited run, write these files into the run directory unless explicit output paths are provided:

- `experiment_provenance.json`
- `comparison.json`
- `experiment_note.md`

`experiment_provenance.json` is the machine-readable source for:

- run identity
- repo state
- tool versions
- command trace
- input hash records
- artifact index
- metrics with source artifacts and source tables
- QA checks
- detected warnings and limitations

`comparison.json` records differences against another run when `--compare-to` is provided. If no comparison target is provided, it still records that no comparison was requested.

`experiment_note.md` is Obsidian-compatible Markdown with YAML frontmatter. It should stay readable in GitHub as plain Markdown.

## CLI

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/acceptance/latest
```

With comparison:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/acceptance/codex-check-peakfix \
  --compare-to runs/acceptance/codex-check
```

With explicit metadata:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/real-todcreek-minimal \
  --case-name "Tod Creek minimal" \
  --workflow-mode "minimal real-data fallback" \
  --objective "Verify real-data SWMM execution and preserve provenance."
```

With an Obsidian vault folder:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/acceptance/latest \
  --obsidian-dir "/path/to/Obsidian/Agentic SWMM/04_Experiments"
```

## Audit rules

- Always preserve relative paths when the artifact is inside the repository.
- Include absolute paths in JSON only when useful for local traceability.
- Record SHA256 for existing file artifacts when feasible.
- Record artifact role, producer, and downstream use.
- Preserve command return codes, stdout paths, stderr paths, and timings when available.
- Treat failed or partial runs as auditable. Missing artifacts should be recorded as missing, not invented.
- Keep metrics tied to source artifacts and source tables.
- For SWMM peak flow, prefer `Node Inflow Summary` / `Maximum Total Inflow`.
- Use `Outfall Loading Summary` / `Max Flow` only as fallback for outfalls.
- Do not extract peak flow from `Node Depth Summary`; that table reports depth and HGL, not flow.

## Relationship to `swmm-end-to-end`

`swmm-end-to-end` is the executor and orchestrator.

`swmm-experiment-audit` is the recorder and auditor.

OpenClaw should run this audit skill after every build/run/QA attempt, even when the workflow stops early or fails. The audit output should reference whatever artifacts exist in the run directory and clearly mark missing or incomplete evidence.

## Obsidian support

The generated `experiment_note.md` is designed for Obsidian:

- YAML frontmatter
- stable headings
- tables for QA, metrics, and artifact index
- relative paths for vault portability
- no chat transcript or conversational content

The note is also valid GitHub Markdown, so it can be committed as an example or exported as supplementary evidence if desired.

Use `--obsidian-dir` to write a copy of the same note into an Obsidian vault folder. This is optional; the run directory remains the canonical audit output location.
