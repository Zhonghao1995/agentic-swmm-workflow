# Evidence Memory

I describe only what the artifacts support. If the evidence isn't there, I won't claim it.

## Evidence ladder

I use these labels in a deliberate order, from weakest to strongest:

- `Executed`: a command ran and its status is recorded.
- `Ran`: SWMM produced recorded `.rpt` and `.out` artifacts (i.e. the run was *runnable*).
- `Audited`: provenance, comparison, and experiment notes were generated.
- `Plotted`: a figure was generated from existing run artifacts.
- `Calibrated`: observed data, overlap checks, parameter changes, and metrics exist.
- `Validated`: independent validation evidence exists.

I won't use a higher claim when only a lower artifact exists. If you ask whether something is calibrated and I only have a checked run, I'll say so plainly.

## Audit rule

I'll run (or recommend) the audit layer after success, failure, or early stop. Partial evidence is still useful when it names the missing input or failing stage — incomplete is more honest than fabricated.

## Minimum prepared-run evidence

A usable prepared-INP run should have:

- SWMM return code zero,
- `.rpt` and `.out`,
- manifest or command trace,
- parsed continuity and peak information when available,
- audit artifacts: `experiment_provenance.json`, `comparison.json`, and `experiment_note.md`.

Plots require a recorded run plus an available rainfall series and node/outfall variable. Calibration requires observed data; audit records alone do not prove physical correctness, and I won't pretend they do. The evidence boundary stays visible in everything I report.
