# Modeling Memory and Controlled Skill Evolution

Agentic SWMM is not only an automation workflow. It is a memory-informed, verification-first modeling system that learns from audited modeling history and changes its own rules only through a human decision.

## Problem

Environmental modeling workflows create many hidden decisions, assumptions, failures, QA checks, and artifacts. If these are not remembered, agentic modeling becomes hard to audit and reproduce. A single successful SWMM execution is not enough to explain which inputs were trusted, which checks passed, which evidence was missing, or which failures repeated across attempts.

## The Audit Layer

`swmm-experiment-audit` records run-level evidence. It consolidates provenance, artifacts, QA checks, metrics, warnings, limitations, comparisons, and Obsidian-compatible experiment notes for individual runs, and writes `model_diagnostics.json` when it can inspect model and report artifacts (high continuity error, node flooding reported by SWMM, invalid subcatchment area, width or imperviousness, missing rain gages, missing subcatchment outlets, suspicious conduit slopes, large routing steps, disconnected outfalls).

The audit layer answers what happened in one run.

## The Memory Store

Everything the project learns lives in one place, `memory/store/` in the workspace (gitignored): append-only JSONL ledgers and a SQLite database (`memory.sqlite`) whose tables are synced indexes of the ledgers plus the sessions and the runs.

- After every audited run the memory hook writes the store: a `parametric_memory.jsonl` row (the run's quantitative fingerprint), a `runs` table row (case, mode, status, QA, peak flow, continuity), a `calibration_memory.jsonl` row when the run has an accepted calibration, a `negative_lessons.jsonl` row when the run failed its continuity gate, and the outcome ledger.
- At the end of every agent session, every tool call that failed is written to `run_failures.jsonl` with its pattern, and when the call right after it worked, with that call as the fix (the same tool with the arguments that changed, a plain retry, or another tool instead).
- `aiswmm memory rebuild` recreates the database from the ledgers and `runs/`; deleting it never loses anything.

The shipped memory (`memory/initial/`: the startup files and the reference tables) and the promoted facts (`memory/facts.md`) are hand-written and change only through a pull request. Nothing the program writes in normal use is tracked.

## How Memory Reaches the Agent

Three paths, each measured in the live campaign that shaped them:

1. **At session start** the shell injects the startup memory, the promoted facts, the previous session's summary, and a digest of the project's recent failures with the fix recorded next to each.
2. **At failure time** the planner looks the failed call's pattern up in the store; when this project recovered from it before, the next model turn carries a `[failure_memory]` item naming the last fix, and the trace records whether the next successful call followed it.
3. **On request** the `recall_memory` tool answers what went wrong before and what was learned (failures with their fixes, negative lessons), and `recall_session_history` searches earlier conversations. Both are read-only and wrapped in `<memory-context>` fences, which the output scrubber strips so historical memory is never parsed as new instructions.

Parametric, calibration and negative-lesson rows also feed the memory-informed defaults, the cross-watershed transfer recommender and the case-adaptive thresholds described in [docs/memory_runtime.md](memory_runtime.md).

## Controlled Skill Refinement

Learning is allowed to change a skill or the shipped memory, but only through a proposal and a human decision:

1. SWMM run
2. experiment audit and deterministic model diagnostics
3. the memory hook writes the store; the session end records failures and fixes
4. evidence gate: the same fix for the same failure pattern in at least three runs across two cases, or a fact the agent recorded with `record_fact`
5. a proposal file under `memory/proposals/` (evidence, the proposed addition, a unified diff of the target: a `SKILL.md`, `memory/initial/operational_memory.md`, or `memory/facts.md`)
6. human review: `aiswmm memory proposals`, then `promote <id>` or `reject <id> --reason`
7. benchmark verification of the promoted change, as an ordinary pull request

The proposal step is intentionally separate from the accepted update step. The program never edits a skill, the initial memory or the facts on its own; a rejected proposal is never made again; a promotion is refused when the target file changed since the proposal was made.

## Safety Boundary

The agent does not autonomously rewrite scientific modeling rules. A proposal is not evidence of correctness. A proposed refinement should only be accepted after human review, existing benchmark verification, and clear evidence that the change improves the workflow without hiding missing data, failed QA, or unsupported assumptions. Audit records are evidence for a run; the store is a record of repeated patterns; neither proves a scientific claim by itself.

## CLI

```bash
aiswmm memory show <case>              # what the store holds about one case
aiswmm memory proposals [--all]        # what awaits a decision
aiswmm memory promote <id>             # apply one proposal (a SKILL.md or the initial memory: in a source checkout)
aiswmm memory reject <id> --reason ... # decline it for good
aiswmm memory rebuild                  # recreate memory.sqlite from the ledgers and runs/
aiswmm doctor                          # the store's row counts and health, section "Memory stores"
```
