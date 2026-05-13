# Codex Runtime Path

This document explains how to use Codex as the primary local development runtime for Agentic SWMM.

Codex can serve as the practical day-to-day runtime for repository development, local SWMM execution, audit generation, Obsidian review, and skill maintenance. OpenClaw and Hermes remain compatible orchestration targets, especially when a user wants a separate agent runtime with MCP-first orchestration.

## Positioning

Use Codex when the goal is:

- local repository development,
- direct script execution,
- code and skill editing,
- inspection of generated artifacts,
- audit and evidence review,
- Obsidian vault updates,
- GitHub synchronization and implementation maintenance.

Use OpenClaw or Hermes when the goal is:

- a separate public agent runtime,
- MCP-centered tool orchestration,
- prompt-level workflow execution outside the Codex development environment,
- runtime demonstrations where the orchestration layer itself is part of the evaluation.

The practical rule is:

```text
Codex = primary local development and audit runtime
OpenClaw / Hermes = compatible external orchestration runtimes
```

This is not a claim that Codex replaces the research idea of OpenClaw/Hermes orchestration in every context. It means Codex can execute and maintain the current repository workflow directly.

## First-Time Setup

From the repository root:

```bash
python3 skills/swmm-experiment-audit/scripts/init_obsidian_vault.py
```

This initializes the default local Obsidian vault:

```text
~/Documents/Agentic-SWMM-Obsidian-Vault
```

Open that folder as an Obsidian vault. The main entry point is:

```text
00_Home/Agentic SWMM Home.md
```

## Codex Audit Loop

After any SWMM build/run/QA attempt, Codex should run:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py --run-dir runs/<case>
```

With a baseline comparison:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/<case> \
  --compare-to runs/<baseline-case>
```

The audit command writes canonical files into the run directory:

```text
runs/<case>/experiment_provenance.json
runs/<case>/comparison.json
runs/<case>/experiment_note.md
```

It also writes a readable note into:

```text
~/Documents/Agentic-SWMM-Obsidian-Vault/20_Audit_Layer/Experiment_Audits
```

and updates:

```text
~/Documents/Agentic-SWMM-Obsidian-Vault/20_Audit_Layer/Experiment Audit Index.md
```

Use `--no-obsidian` only when a run should create repo-local audit files without updating the local vault.

## Recommended Codex Workflow

1. Read the relevant skill:
   - `skills/swmm-end-to-end/SKILL.md`
   - module skill files under `skills/`
   - `skills/swmm-experiment-audit/SKILL.md`
2. Choose the execution mode:
   - full modular path,
   - prepared-input path,
   - minimal real-data fallback,
   - benchmark path,
   - uncertainty path.
3. Run the existing script or MCP-backed module path.
4. Preserve outputs under `runs/<case>/`.
5. Run `audit_run.py`.
6. Review the Obsidian audit note and `experiment_provenance.json`.
7. Update claims only when the audit evidence supports them.
8. If a repeated pattern appears, record a proposed skill change rather than silently changing research claims.

## Evidence Discipline

Codex should treat these files as evidence:

- SWMM `.inp`, `.rpt`, and `.out` files,
- manifests and command traces,
- QA JSON files,
- `experiment_provenance.json`,
- `comparison.json`,
- generated figures and tables,
- Git commit hashes.

Codex should not treat these as final evidence by themselves:

- chat summaries,
- unverified ideas,
- partial runs without clear warnings,
- copied data directories that were not executed,
- minimal smoke tests presented as full validation.

## Relationship To OpenClaw / Hermes

The top-level execution contract still lives in:

```text
skills/swmm-end-to-end/SKILL.md
docs/openclaw-execution-path.md
agent/memory/
```

Codex can follow the same skill contract locally. OpenClaw and Hermes can use the same contract as external orchestration runtimes.

The difference is operational:

| Runtime | Best Use | Evidence Rule |
|---|---|---|
| Codex | local development, scripts, edits, audit, Obsidian, GitHub sync | verify with local files, commands, diffs, tests, and audit outputs |
| OpenClaw | MCP-first public orchestration path | follow `docs/openclaw-execution-path.md` and preserve run artifacts |
| Hermes | compatible agent orchestration experiments | use the same skill and audit contracts |

## Minimal Codex Smoke Test

Initialize the vault:

```bash
python3 skills/swmm-experiment-audit/scripts/init_obsidian_vault.py
```

Run an existing audit:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/real-todcreek-minimal \
  --workflow-mode "minimal real-data fallback"
```

Then open:

```text
~/Documents/Agentic-SWMM-Obsidian-Vault/20_Audit_Layer/Experiment Audit Index.md
```

If the run appears in the index and the note contains QA, metrics, artifact paths, and warnings, the Codex audit loop is working.
