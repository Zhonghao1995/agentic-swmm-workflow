# Modeling Memory and Controlled Skill Evolution

Agentic SWMM is not only an automation workflow. It is a memory-informed, verification-first modeling system that can learn from audited modeling history through controlled skill refinement.

## Problem

Environmental modeling workflows create many hidden decisions, assumptions, failures, QA checks, and artifacts. If these are not remembered, agentic modeling becomes hard to audit and reproduce. A single successful SWMM execution is not enough to explain which inputs were trusted, which checks passed, which evidence was missing, or which failures repeated across attempts.

## Existing Audit Layer

`swmm-experiment-audit` records run-level evidence. It consolidates provenance, artifacts, QA checks, metrics, warnings, limitations, comparisons, and Obsidian-compatible experiment notes for individual runs.

The audit layer answers what happened in one run.

## New Modeling-Memory Layer

`swmm-modeling-memory` reads multiple audit records and turns them into reusable project memory. It scans historical `experiment_provenance.json`, `comparison.json`, and `experiment_note.md` files, tolerates partial runs, and summarizes repeated assumptions, QA issues, missing evidence, run-to-run differences, failure patterns, and successful practices.

The modeling-memory skill does not automatically rewrite existing skills. It analyzes historical audit records and generates proposed refinements for relevant workflow skills, such as end-to-end orchestration, audit reporting, QA verification, model building, or result parsing.

The modeling-memory layer answers what keeps happening across runs.

## Controlled Skill Refinement

The intended controlled loop is:

1. SWMM run
2. experiment audit
3. Obsidian-compatible note
4. modeling-memory summarization
5. failure-pattern extraction
6. skill update proposal
7. human review
8. benchmark verification
9. accepted skill update

The proposal step is intentionally separate from the accepted update step. Modeling memory may suggest where a workflow or skill appears weak, but it does not modify scientific rules or repository skills by itself. Proposed updates are accepted only after human review and benchmark verification.

## Safety Boundary

The agent does not autonomously rewrite scientific modeling rules. Skill update proposals are not evidence of correctness. A proposed refinement should only be accepted after human review, existing benchmark verification, and clear evidence that the change improves the workflow without hiding missing data, failed QA, or unsupported assumptions.

## Example CLI Usage

Main command:

```bash
python3 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs \
  --out-dir memory/modeling-memory
```

Optional Obsidian export:

```bash
python3 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs \
  --out-dir memory/modeling-memory \
  --obsidian-dir "/path/to/Obsidian/Agentic SWMM/05_Modeling_Memory"
```
