# Evidence Memory

## Evidence-first rule

Every Agentic SWMM run should make a clear distinction between:

- what was directly executed,
- what was parsed from SWMM outputs,
- what was inferred,
- what is missing,
- what is outside the current evidence boundary.

## Audit rule

Always run or recommend the audit layer after a workflow attempt:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py --run-dir runs/<case>
```

For baseline or scenario comparison:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py \
  --run-dir runs/<case> \
  --compare-to runs/<baseline-case>
```

The audit should preserve partial evidence when a workflow fails or stops early.

## QA gates

A SWMM run should not be treated as usable until these minimum checks are available:

- SWMM return code is zero,
- `.rpt` exists,
- `.out` exists,
- continuity parses,
- peak flow parses from the correct SWMM report section,
- the run manifest identifies commands and artifacts.

For calibration, also require:

- observed flow parses,
- simulated and observed periods overlap enough for the chosen metric,
- chosen parameter changes are recorded.

## Claim boundaries

Use precise language:

- "Executed" means the command ran.
- "Parsed" means the workflow extracted a metric from an artifact.
- "Checked" means QA outputs were generated and reviewed.
- "Audited" means provenance and comparison files were written.
- "Validated" requires independent evidence beyond a successful run.

## Controlled skill evolution

Skill update proposals are not evidence of correctness. A skill refinement should only be accepted after human review and benchmark verification.

## Known boundary

The repository supports prepared-input and structured raw GIS-to-INP validation paths. It should not claim complete greenfield watershed and pipe-network generation from arbitrary DEM, soil, land use, and drainage data unless that evidence has been produced for the case.
