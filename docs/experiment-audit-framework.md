# Experiment Audit Framework

This document defines the audit layer for Agentic SWMM.

The audit layer does not run SWMM. It consolidates the evidence produced by the workflow into machine-readable provenance, run-to-run comparison records, and Obsidian-compatible notes.

## Purpose

Agentic SWMM runs produce useful evidence in several places:

- stage manifests
- SWMM input, report, and output files
- QA JSON files
- stdout and stderr logs
- acceptance reports
- calibration summaries

Without a consolidation layer, these records are auditable but dispersed. `swmm-experiment-audit` turns them into a single experiment record.

## Required outputs

Every audited run should produce:

```text
experiment_provenance.json
comparison.json
experiment_note.md
```

`experiment_provenance.json` is the canonical machine-readable audit record.

`comparison.json` records baseline/scenario or before/after differences. If no comparison target is provided, it explicitly records that no comparison was requested.

`experiment_note.md` is Obsidian-compatible Markdown with YAML frontmatter and stable sections for review.

## Provenance schema

The provenance record should include:

- run identity
- case name and workflow mode
- run directory
- Git branch, commit, and working-tree status
- tool versions
- command trace
- input hashes
- artifact index
- metrics and metric sources
- QA checks
- warnings

## Artifact index

Artifacts are first-class audit records. Each artifact should include:

- artifact ID
- role
- relative path
- absolute path when available
- existence status
- SHA256 when available
- producer
- downstream use

This lets a reviewer trace a result from source input to command, artifact, metric, and interpretation.

## Metric source contract

Metrics must preserve source context.

For SWMM peak flow:

- preferred source: `Node Inflow Summary` / `Maximum Total Inflow`
- fallback source: `Outfall Loading Summary` / `Max Flow`
- forbidden source for flow: `Node Depth Summary`

`Node Depth Summary` reports depth and HGL. It must not be used as peak-flow evidence.

For continuity:

- use SWMM report continuity tables
- preserve runoff and flow-routing continuity errors separately

## OpenClaw integration

`swmm-end-to-end` should call:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py --run-dir runs/<case>
```

With comparison:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/<case> \
  --compare-to runs/<baseline-case>
```

With an Obsidian vault folder:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/<case> \
  --obsidian-dir "/path/to/Obsidian/Agentic SWMM/04_Experiments"
```

This audit call should happen after success, failure, or early stop. Partial evidence is still useful evidence.

## Obsidian support

The generated note is intended to be copied or linked into an Obsidian vault without conversion.

It should not contain chat transcripts or conversational history. It should only contain run evidence, metric provenance, QA results, comparison records, warnings, and concise interpretation.

When `--obsidian-dir` is provided, the CLI writes a copy of `experiment_note.md` into that folder while keeping the run directory as the canonical audit location.
