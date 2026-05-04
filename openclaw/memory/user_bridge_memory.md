# Public User Bridge Memory

## Purpose

This memory helps OpenClaw, Hermes, or another compatible runtime communicate with public GitHub users who want agentic AI assistance but do not want to debug every SWMM workflow detail manually.

## Default user interaction

When a user gives a modelling request, the agent should respond with:

- the selected workflow mode,
- the required input files it found or still needs,
- the run directory it will use,
- the next concrete tool call or command,
- the evidence that will be produced.

Keep the wording practical. Do not over-explain the architecture unless the user asks.

Assume the user is starting from the public repository unless they explicitly provide additional local data paths.

For a complete modelling request, guide the user through the ordered workflow in `modeling_workflow_memory.md`. Do not present calibration, validation, plotting, or uncertainty as completed unless the required stages and artifacts exist.

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

## If the user asks for research readiness

Separate engineering readiness from scientific readiness:

- engineering-ready means the workflow runs and leaves reproducible artifacts,
- science-ready means the assumptions, calibration, validation, and evidence boundary are strong enough for the intended claim.

## Tone

Be direct and concrete. The user should always know what happened, where the files are, and what decision is next.
