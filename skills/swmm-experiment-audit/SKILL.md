---
name: swmm-experiment-audit
description: Consolidate Agentic SWMM run artifacts into auditable provenance, comparison records, and local Obsidian audit notes. Use after any SWMM build/run/QA attempt, successful or failed, when an agent or CLI workflow needs a traceable record of inputs, commands, artifacts, metrics, QA checks, run-to-run differences, and first-user-friendly Obsidian visualization.
---

# SWMM Experiment Audit

Part of [Agentic SWMM](https://github.com/Zhonghao1995/agentic-swmm-workflow) — install the project first for the executable toolchain (aiswmm CLI, SWMM solver, MCP servers).

## What this skill provides

- A standard audit layer for Agentic SWMM runs.
- Consolidation of dispersed `manifest.json`, QA JSON, logs, metrics, and artifact paths.
- Machine-readable outputs for reproducibility and review.
- Obsidian-compatible Markdown notes for human research records.
- Default local Obsidian export into a clean English audit vault.
- Automatic update of the Obsidian `Experiment Audit Index`.
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

For every audited run, write these files into the run's `09_audit/` directory unless explicit output paths are provided:

- `experiment_provenance.json` — machine-readable provenance (immutable).
- `experiment_note.md` — human-readable Obsidian digest.
- `model_diagnostics.json` — deterministic SWMM screening checks.
- `comparison.json` — run-to-run comparison (only when `--compare-to` is given).

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

The direct script (`audit_run.py`) writes a copy of the audit note to the
Obsidian vault by default; pass `--no-obsidian` to suppress it. The
canonical CLI (`aiswmm audit`) does **not** export to Obsidian by default;
pass `--obsidian` to enable it.

When Obsidian export is active, the note is written into:

```text
~/Documents/Agentic-SWMM-Obsidian-Vault/20_Audit_Layer/Experiment_Audits
```

and the index is updated at:

```text
~/Documents/Agentic-SWMM-Obsidian-Vault/20_Audit_Layer/Experiment Audit Index.md
```

## CLI

The canonical CLI is `aiswmm audit`. It wraps the underlying script with
backup of prior audit files, MOC regeneration (`runs/INDEX.md`), and the
M2 audit → memory auto-trigger. The direct script path is also supported
and is the primary option for MCP / Mode-0 calls.

### Canonical CLI (`aiswmm audit`)

Obsidian export is **opt-in** with `aiswmm audit` — pass `--obsidian` to
copy the note to the default vault. Without `--obsidian` the note is written
only into `09_audit/` inside the run directory.

```bash
aiswmm audit --run-dir runs/acceptance/latest
```

With comparison:

```bash
aiswmm audit --run-dir runs/acceptance/codex-check-peakfix \
  --compare-to runs/acceptance/codex-check
```

With explicit metadata and Obsidian export:

```bash
aiswmm audit --run-dir runs/real-todcreek-minimal \
  --case-name "Tod Creek minimal" \
  --workflow-mode "minimal real-data fallback" \
  --objective "Verify real-data SWMM execution and preserve provenance." \
  --obsidian
```

Skip the M2 memory auto-trigger (useful for acceptance / benchmark runs):

```bash
aiswmm audit --run-dir runs/acceptance/latest --no-memory
```

Run-to-run comparison with the standalone verb:

```bash
aiswmm compare --run-a runs/baseline --run-b runs/scenario
```

### Direct script path (`python3 scripts/audit_run.py`)

Use when calling from MCP or when the full `aiswmm` install is unavailable.
Obsidian export is **on by default** in the script; disable with `--no-obsidian`.

Initialize a first-user Obsidian vault:

```bash
python3 skills/swmm-experiment-audit/scripts/init_obsidian_vault.py
```

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/acceptance/latest \
  --no-obsidian
```

With comparison:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/acceptance/codex-check-peakfix \
  --compare-to runs/acceptance/codex-check \
  --no-obsidian
```

With explicit metadata including `--case-id` (records the case slug in
`experiment_provenance.json` for cross-session memory recall):

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/real-todcreek-minimal \
  --case-id tod-creek \
  --case-name "Tod Creek minimal" \
  --workflow-mode "minimal real-data fallback" \
  --objective "Verify real-data SWMM execution and preserve provenance." \
  --no-obsidian
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

The agent should run this audit skill after every build/run/QA attempt, even when the workflow stops early or fails. The audit output should reference whatever artifacts exist in the run directory and clearly mark missing or incomplete evidence.

## Obsidian support

The generated `experiment_note.md` is designed for Obsidian:

- YAML frontmatter
- stable headings
- tables for QA, metrics, and artifact index
- relative paths for vault portability
- no chat transcript or conversational content

The note is also valid GitHub Markdown, so it can be committed as an example or exported as supplementary evidence if desired.

The default local vault is:

```text
~/Documents/Agentic-SWMM-Obsidian-Vault
```

It is organized for first-time Obsidian use:

```text
00_Home/
10_Memory_Layer/
20_Audit_Layer/
30_Evidence_Layer/
40_Skill_Evolution/
90_Templates/
```

The direct script always writes canonical outputs into the run directory and
copies the note to the Obsidian vault unless `--no-obsidian` is given. The
canonical CLI (`aiswmm audit`) writes canonical outputs only; add `--obsidian`
to also copy to the vault.

In both paths, use `--obsidian-dir` and `--obsidian-index` to target a vault
location other than the default `~/Documents/Agentic-SWMM-Obsidian-Vault`.
