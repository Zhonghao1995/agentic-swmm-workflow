# Evidence Memory

aiswmm should describe only what the artifacts support.

## Evidence ladder

- `Executed`: a command ran and its status is recorded.
- `Ran`: SWMM produced recorded `.rpt` and `.out` artifacts.
- `Audited`: provenance, comparison, and experiment notes were generated.
- `Plotted`: a figure was generated from existing run artifacts.
- `Calibrated`: observed data, overlap checks, parameter changes, and metrics exist.
- `Validated`: independent validation evidence exists.

Do not use a higher claim when only a lower artifact exists.

## Audit rule

Run or recommend the audit layer after success, failure, or early stop. Partial evidence is still useful when it names the missing input or failing stage.

## Minimum prepared-run evidence

A usable prepared-INP run should have:

- SWMM return code zero,
- `.rpt` and `.out`,
- manifest or command trace,
- parsed continuity and peak information when available,
- audit artifacts: `experiment_provenance.json`, `comparison.json`, and `experiment_note.md`.

Plots require a recorded run plus an available rainfall series and node/outfall variable. Calibration requires observed data; audit records alone do not prove physical correctness.
