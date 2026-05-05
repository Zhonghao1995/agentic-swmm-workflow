# Skill Update Proposals

Generated at UTC: `2026-05-05T11:17:07+00:00`

Agentic SWMM is not only an automation workflow; it is a memory-informed, verification-first modeling system that can learn from audited modeling history through controlled skill refinement.

This document is only a proposal. It is not an automatic skill update and it is not evidence of correctness.

The modeling-memory skill analyzes historical audit records and generates proposed refinements for relevant workflow skills, such as end-to-end orchestration, audit reporting, QA verification, model building, or result parsing.

Accepted skill changes require human review and benchmark verification before any existing `SKILL.md` is modified.

## `comparison_mismatch`

- Potential skill or workflow step: Run comparison
- Relevant workflow skill(s): `swmm-experiment-audit`, `swmm-end-to-end`
- Why it may need improvement: Review whether mismatches are expected scenario differences or regressions that need acceptance criteria.
- Evidence runs: `codex-check-peakfix`
- Required control: human review plus benchmark verification before accepting any skill refinement.

## `continuity_parse_missing`

- Potential skill or workflow step: Continuity parsing
- Relevant workflow skill(s): `swmm-runner`, `swmm-experiment-audit`
- Why it may need improvement: Check whether continuity tables are absent, malformed, or not referenced in the run manifest.
- Evidence runs: `real-todcreek-minimal`, `runner-check`, `runner-fixed`
- Required control: human review plus benchmark verification before accepting any skill refinement.

## `missing_inp`

- Potential skill or workflow step: SWMM build/input handoff
- Relevant workflow skill(s): `swmm-builder`, `swmm-end-to-end`
- Why it may need improvement: Ensure the workflow records where the runnable INP should be produced before SWMM execution.
- Evidence runs: `runner-check`, `runner-fixed`
- Required control: human review plus benchmark verification before accepting any skill refinement.

## `partial_run`

- Potential skill or workflow step: Workflow stop handling
- Relevant workflow skill(s): `swmm-end-to-end`, `swmm-experiment-audit`
- Why it may need improvement: Make partial-run handoff to audit explicit so incomplete evidence is still reusable.
- Evidence runs: `real-todcreek-minimal`, `runner-check`, `runner-fixed`
- Required control: human review plus benchmark verification before accepting any skill refinement.

## `peak_flow_parse_missing`

- Potential skill or workflow step: Peak-flow parsing
- Relevant workflow skill(s): `swmm-runner`, `swmm-experiment-audit`
- Why it may need improvement: Check whether the correct SWMM report section is available and whether the parser should report a clearer boundary.
- Evidence runs: `runner-check`, `runner-fixed`
- Required control: human review plus benchmark verification before accepting any skill refinement.
