# Repository Map

This repository is the Agentic SWMM workflow layer: a compact set of skills, scripts, examples, benchmarks, audit records, and modeling-memory artifacts for reproducible SWMM work.

The development checkout is your local clone of this repo, e.g.:

```text
~/agentic-swmm-workflow
```

The private GitHub remote is:

```text
Zhonghao1995/agentic-swmm-workflow-private
```

The public-facing repository remains:

```text
Zhonghao1995/agentic-swmm-workflow
```

## Organization Principle

Skills are grouped by workflow stage, not by every algorithm. New methods should usually become scripts, examples, or strategy options inside an existing stage skill.

For example, entropy-guided LID placement belongs inside `swmm-lid-optimization`, not in a separate `swmm-entropy-lid` skill.

## Top-Level Folders

| Folder | Role |
|---|---|
| `skills/` | Reusable workflow-stage skills and their scripts, examples, tests, and optional MCP scaffolds. |
| `scripts/` | Repository-level bootstrap, acceptance, benchmark, and real-case runner scripts. |
| `examples/` | Small reusable input fixtures and prepared examples. |
| `docs/` | Human-readable workflow, validation, audit, memory, runtime, and planning documents. |
| `runs/` | Generated benchmark, acceptance, audit, and experiment outputs. |
| `memory/modeling-memory/` | Generated project modeling memory derived from audited runs. |
| `agentic-ai/memory/` | Public Agentic AI memory preload files for project identity and evidence posture. |
| `tests/` | Top-level lightweight tests for shared audit, runner, and memory behavior. |

## Skill Layer

Keep these as the main skill boundaries:

| Skill | Main Question | Current Form |
|---|---|---|
| `swmm-gis` | How are subcatchment GIS inputs preprocessed? | CLI skill, MCP-oriented scaffolding where present. |
| `swmm-network` | How are junctions, conduits, outfalls, and network QA handled? | CLI skill with MCP server. |
| `swmm-params` | How are land-use and soil inputs mapped to SWMM parameters? | CLI/reference skill. |
| `swmm-climate` | How is rainfall formatted for SWMM? | CLI skill with MCP server. |
| `swmm-builder` | How is a SWMM INP assembled from prepared artifacts? | Builder skill. |
| `swmm-runner` | How is SWMM executed and parsed reproducibly? | CLI skill with MCP server. |
| `swmm-plot` | How are rainfall-runoff figures generated? | CLI skill with MCP server. |
| `swmm-calibration` | Which parameters best match observations? | CLI skill with MCP server. |
| `swmm-uncertainty` | How much output spread follows from uncertain inputs? | CLI skill; future MCP wrapper documented. |
| `swmm-lid-optimization` | Which LID type, size, and placement choices improve objectives? | CLI skill; future MCP wrapper documented. |
| `swmm-experiment-audit` | What happened in one run, and what evidence supports it? | CLI audit skill. |
| `swmm-modeling-memory` | What keeps happening across audited runs? | CLI memory summarizer. |
| `swmm-end-to-end` | Which module should run next in an agent-orchestrated workflow? | Top-level orchestration skill. |

## LID Skill Internal Layout

LID-related work should stay under:

```text
skills/swmm-lid-optimization/
```

Use this internal structure:

| Layer | Files | Purpose |
|---|---|---|
| Priority diagnostics | `scripts/entropy_lid_priority.py` | Convert subcatchment metric tables or D8 raster diagnostics into `lid_priority_score`. |
| Scenario generation | `scripts/lid_scenario_builder.py` | Insert `[LID_CONTROLS]` and `[LID_USAGE]`, rank candidates, and write scenario manifests. |
| Examples | `examples/*.json`, `examples/*.csv` | Small configs and priority tables for reproducible smoke tests. |
| Tests | `tests/test_*.py` | Keep ranking, scenario generation, and priority scoring behavior stable. |
| Benchmark execution | `scripts/benchmarks/run_tecnopolo_lid_placement_smoke.py` | Run generated scenarios through SWMM and score outputs. |

Do not create new skills for each LID strategy. Random placement, imperviousness-based placement, flooding-based placement, entropy-guided placement, cost-effectiveness, and resilience scoring should be strategy options inside `swmm-lid-optimization`.

## Memory and Audit Layers

There are three memory-like systems, each with a different job:

| Layer | Path | Job |
|---|---|---|
| Codex long-term memory | `~/.codex/memories/` | Remembers user/project history across Codex sessions (per-user, platform-agnostic). |
| Agentic SWMM modeling memory | `memory/modeling-memory/` | Summarizes audited SWMM runs, repeated issues, and skill update proposals. |
| Agentic AI preload memory | `agentic-ai/memory/` | Gives external agent runtimes stable project identity, operating posture, and evidence boundaries. |

The audit layer sits before modeling memory:

```text
SWMM run -> swmm-experiment-audit -> 09_audit/{experiment_provenance.json,comparison.json,experiment_note.md} -> swmm-modeling-memory
```

Audit records are evidence for a run. Modeling memory is a summary of repeated patterns. Neither one proves a scientific claim by itself.

### Audit-artefact location invariant

Every audited run dir writes its audit artefacts into a single canonical subdir, regardless of where the run dir lives in `runs/` and regardless of which stage-numbering scheme that run dir uses:

```text
<run-dir>/                                # any depth under runs/
└── 09_audit/
    ├── experiment_note.md                # required
    ├── experiment_provenance.json        # required (schema_version: 1.1)
    ├── comparison.json                   # optional
    ├── model_diagnostics.json            # optional
    └── experiment_note.<utc-ts>.md.bak   # prior versions on re-audit
```

Rules:

- The only invariant enforced by `agentic_swmm.audit.run_folder_layout.validate()` is the presence of `09_audit/experiment_note.md` and `09_audit/experiment_provenance.json` for SWMM run dirs. No other stage subdir is required.
- Filenames inside `09_audit/` are unprefixed. On re-audit, the prior version is renamed `<name>.<utc-ts>.<ext>.bak` before the new file is written.
- Chat sessions live at `runs/YYYY-MM-DD/HHMMSS_<slug>_chat/` and carry `chat_note.md` (Obsidian frontmatter `type: chat-session`) instead of a SWMM `09_audit/`. Chat sessions do not produce `final_report.md`.
- Zombie `runs/agent/agent-<digits>/` dirs are archived to `runs/.archive/` via `scripts/archive_zombies.py`. `runs/agent/interactive/` (the current CLI output target) stays in place.

### Obsidian MOC

After every successful `aiswmm audit`, `agentic_swmm.audit.moc_generator.generate_moc()` regenerates `runs/INDEX.md`. The MOC walks the tree breadth-first via `RunFolderLayout.discover` (unlimited depth, so nested `external-case-candidates/<bucket>/<month>/<runner>/` cases surface alongside top-level ones), and emits two tables — by date and by first-segment bucket — plus an `Unaudited run dirs` section. The filesystem layout under `runs/` is otherwise untouched.

### Migration scripts

| Script | Job |
|---|---|
| `scripts/migrate_audit_layout.py` | Converge legacy audit artefacts (P1 root files, P2 bucket root, P3 deeply nested, P4 unnumbered `audit/` from GIS-style cases, P5 empty `06_audit/`) into `09_audit/`. `--dry-run` default; `--apply` to commit. Idempotent. |
| `scripts/archive_zombies.py` | Move `runs/agent/agent-<digits>/` dirs under `runs/.archive/`. Never touches `runs/agent/interactive/`. `--dry-run` default; `--apply` to commit. Idempotent. |

Both scripts use `git mv` when the tree is tracked so the moves are reversible via `git revert` + `git mv` back (PRD Rollback).

## Documentation Entry Points

| Document | Use When |
|---|---|
| `docs/openclaw-execution-path.md` | You need the stage-by-stage external agent execution contract. |
| `docs/codex-runtime.md` | You need Codex-specific local runtime behavior. |
| `docs/validation-evidence.md` | You need benchmark evidence boundaries and runnable verification paths. |
| `docs/lid-optimization-workflow.md` | You need the LID scenario-generation and evaluation workflow. |
| `docs/lid-entropy-decision-support-plan.md` | You need the second-paper LID/entropy planning logic. |
| `docs/calibration-uncertainty-workflow.md` | You need calibration and uncertainty boundaries. |
| `docs/experiment-audit-framework.md` | You need audit artifact contracts. |
| `docs/modeling-memory-and-skill-evolution.md` | You need modeling-memory and controlled skill evolution rules. |

## Evidence Boundary

The repository is strongest as a reproducible, auditable workflow for:

- prepared-input SWMM execution;
- structured raw GIS-to-INP benchmark paths;
- uncertainty and entropy propagation;
- LID scenario generation and placement evaluation;
- audit records and modeling-memory summaries.

Do not overstate it as fully automatic greenfield watershed and pipe-network generation unless a case-specific benchmark has validated those inputs, outputs, and QA checks.
