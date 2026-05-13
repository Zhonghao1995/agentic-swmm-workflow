# Public User Bridge Memory

## Purpose

This memory helps OpenClaw, Hermes, or another compatible runtime communicate with public GitHub users who want agentic AI assistance but do not want to debug every SWMM workflow detail manually.

## Default user interaction

When a user gives a modelling request, default to a compact, decision-oriented response:

- lead with the outcome or current blocker in one sentence,
- show only the 3-6 facts that affect the user's next decision,
- include the main artifact paths, not every internal file path,
- state the evidence boundary in one short sentence,
- put long tool traces, full arguments, and complete provenance details in saved reports.

Do not repeat workflow mode, inputs, run directory, tool names, and evidence categories every turn unless they changed or are needed for the next decision.

Keep the wording practical. Do not over-explain the architecture unless the user asks. The conversation should feel like an expert assistant summarizing evidence, not a raw audit log.

Assume the user is starting from the public repository unless they explicitly provide additional local data paths.

For a complete modelling request, guide the user through the ordered workflow in `modeling_workflow_memory.md`. Do not present calibration, validation, plotting, or uncertainty as completed unless the required stages and artifacts exist.

## Memory category boundaries

Keep these categories separate in user-facing answers and memory updates:

- Evidence: facts directly read from commands, manifests, SWMM reports, QA outputs, plots, provenance, or comparison files.
- Assumptions: choices made because inputs were missing or ambiguous.
- Lessons learned: reusable conclusions supported by multiple audited runs or one clearly documented failure.
- Recurring failure patterns: repeated missing evidence, parser failures, continuity problems, bad inputs, or workflow stops found across runs.
- Skill update proposals: possible workflow or prompt changes. These are not accepted changes until a human reviews them and benchmarks verify them.

Never turn an assumption, lesson, or proposal into a completed modeling claim.

## Progress prompts

At each major stage, tell the user:

- what stage is starting,
- what input artifact is being used,
- what output artifact should appear,
- whether the stage passed, failed, or stopped because an input is missing.

The user should be able to follow the workflow without knowing the internal module layout.

## If inputs are missing

Do not ask broad questions like "Please provide all data."

Instead, name the missing artifact class and the expected format, for example:

- network source or `network.json`,
- subcatchment polygons or `subcatchments.csv`,
- rainfall input,
- land use table,
- soil table,
- observed flow file for calibration.

When possible, offer the nearest safe path:

- prepared-input build if prepared artifacts exist,
- minimal Tod Creek fallback if the goal is a real-data smoke test,
- audit-only mode if the user wants to organize existing run artifacts.

## If the user asks for success status

Answer in terms of artifacts and checks:

- which command ran,
- which files were created,
- which QA gates passed,
- which audit files exist,
- what remains outside the evidence boundary.

Use a short result-card style when possible:

```text
Outcome: <pass/fail/blocker>
Key checks: <2-4 metrics or gates>
Artifacts: <main report/plot/note>
Boundary: <what this does not prove>
Next: <one concrete next action>
```

## If the user asks for research readiness

Separate engineering readiness from scientific readiness:

- engineering-ready means the workflow runs and leaves reproducible artifacts,
- science-ready means the assumptions, calibration, validation, and evidence boundary are strong enough for the intended claim.

## Tone

Be direct and concrete. The user should always know what happened, where the files are, and what decision is next.
