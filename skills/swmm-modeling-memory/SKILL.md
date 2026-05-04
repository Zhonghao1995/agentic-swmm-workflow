---
name: swmm-modeling-memory
description: Read historical Agentic SWMM experiment audit artifacts and summarize repeated assumptions, QA issues, failures, missing evidence, run-to-run differences, lessons learned, and controlled skill update proposals. Use downstream of swmm-experiment-audit when multiple audited runs exist or when a user asks for modeling memory, failure-pattern extraction, lessons learned, or human-reviewed skill refinement proposals.
---

# SWMM Modeling Memory

## What this skill provides

- A downstream memory layer for audited Agentic SWMM runs.
- Deterministic summaries of repeated assumptions, QA issues, failures, missing evidence, and run-to-run differences.
- Human-readable lessons learned from previous audit records.
- Controlled skill update proposals that require human review and benchmark verification.

This skill does not run SWMM, build SWMM models, modify existing skills, or claim autonomous self-improvement.

Agentic SWMM is not only an automation workflow. It is a memory-informed, verification-first modeling system that can learn from audited modeling history through controlled skill refinement.

## When to use this skill

Use this skill after `swmm-experiment-audit` has produced run-level artifacts such as:

- `experiment_provenance.json`
- `comparison.json`
- `experiment_note.md`

Use it when:

- multiple audited runs exist,
- the user wants lessons learned across runs,
- the user asks for recurring failure patterns or QA issues,
- the user wants evidence-informed skill refinement proposals.

The proposals may point to relevant workflow skills such as end-to-end orchestration, audit reporting, QA verification, model building, or result parsing. They are not accepted changes.

## Output contract

The script writes these files to the selected modeling-memory output directory:

- `modeling_memory_index.json`
- `modeling_memory_index.md`
- `lessons_learned.md`
- `skill_update_proposals.md`
- `benchmark_verification_plan.md`

The JSON index is the machine-readable source. The Markdown files are human-readable and can be copied to Obsidian with `--obsidian-dir`.

## CLI

```bash
python3 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs \
  --out-dir memory/modeling-memory
```

With optional Obsidian export:

```bash
python3 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs \
  --out-dir memory/modeling-memory \
  --obsidian-dir "/path/to/Obsidian/Agentic SWMM/05_Modeling_Memory"
```

## Safety rules

- Read existing audit artifacts only.
- Tolerate partial and failed runs.
- Do not modify any existing `SKILL.md` files.
- Do not modify benchmark behavior or audit output formats.
- Do not write outside `--out-dir` or the optional `--obsidian-dir`.
- Treat skill update proposals as proposals only.
- Accept real skill refinements only after human review and benchmark verification.

## Relationship to `swmm-experiment-audit`

`swmm-experiment-audit` records evidence for one run.

`swmm-modeling-memory` reads many audited runs and turns repeated evidence patterns into reusable project memory.

The intended controlled loop is:

1. Run SWMM or attempt a workflow.
2. Audit the run.
3. Preserve an Obsidian-compatible experiment note.
4. Summarize modeling memory across audited runs.
5. Extract recurring failure patterns.
6. Generate a skill update proposal.
7. Review the proposal as a human.
8. Verify with existing benchmarks before accepting any skill change.
