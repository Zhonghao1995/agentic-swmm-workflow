<!-- schema_version: 1.1 -->
# Lessons Learned

Generated at UTC: `2026-05-06T10:16:48+00:00`

This synthesis is derived from historical experiment audit artifacts. It is project memory, not proof that a model is calibrated or validated.

## Repeated Failure Patterns
- `no_detected_failure`: 7 run(s)
- `continuity_parse_missing`: 5 run(s)
- `partial_run`: 5 run(s)
- `missing_inp`: 2 run(s)
- `peak_flow_parse_missing`: 2 run(s)

## Repeated Assumptions
- No repeated assumptions were detected in the audited records.

## Repeated Missing Evidence
- `acceptance_report_json` missing in 9 run(s)
- `acceptance_report_md` missing in 9 run(s)
- `network_qa` missing in 9 run(s)
- `continuity_qa` missing in 7 run(s)
- `peak_qa` missing in 7 run(s)
- `builder_manifest` missing in 7 run(s)
- `runner_manifest` missing in 5 run(s)
- `runner_stderr` missing in 2 run(s)
- `runner_stdout` missing in 2 run(s)
- `model_inp` missing in 2 run(s)

## Repeated QA Issues
- QA status `pass`: 12 run(s)

## Run-to-Run Difference Signals
- Comparison status `not_requested`: 12 run(s)

## Repeated SWMM Model Diagnostics
- No repeated deterministic SWMM model diagnostics were detected.

## Successful Practices
- `codex-check-peakfix` preserved audit evidence with QA `pass` and comparison `not_requested`.
- `codex-ci-audit-local` preserved audit evidence with QA `pass` and comparison `not_requested`.
- `codex-status-20260503` preserved audit evidence with QA `pass` and comparison `not_requested`.
- `generate-swmm-inp-raw-path` preserved audit evidence with QA `pass` and comparison `not_requested`.
- `tecnopolo-199401-prepared` preserved audit evidence with QA `pass` and comparison `not_requested`.
- `tuflow-swmm-module03-raw-path` preserved audit evidence with QA `pass` and comparison `not_requested`.
- `tecnopolo-199401-prepared` preserved audit evidence with QA `pass` and comparison `not_requested`.

## continuity_parse_missing

Observed in 5 run(s): `real-todcreek-minimal`, `runner-check`, `runner-fixed`, `tecnopolo-lid-placement-smoke`, `tecnopolo-mc-uncertainty-smoke`.

The continuity error line is absent from the RPT file. Most commonly this is the side-effect of a `partial_run` — SWMM exited before writing the continuity tables. Check the runner manifest and stderr for early failure, and re-run with verbose logging if needed.

Recall this section via `recall_memory("continuity_parse_missing")` or by searching with `recall_memory_search`.

## missing_inp

Observed in 2 run(s): `runner-check`, `runner-fixed`.

The runnable INP path was not produced before SWMM execution was attempted. Check the builder manifest: the workflow must record where the INP should land before the runner is invoked. Related skills: `swmm-builder`, `swmm-end-to-end`.

Recall this section via `recall_memory("missing_inp")` or by searching with `recall_memory_search`.

## partial_run

Observed in 5 run(s): `real-todcreek-minimal`, `runner-check`, `runner-fixed`, `tecnopolo-lid-placement-smoke`, `tecnopolo-mc-uncertainty-smoke`.

SWMM exited before writing all expected artifacts (RPT, OUT, or both). Verify that the runner captured stderr and stdout, and that the runner manifest records the exit code. A partial run is a runnable failure mode, not a calibration result.

Recall this section via `recall_memory("partial_run")` or by searching with `recall_memory_search`.

## peak_flow_parse_missing

Observed in 2 run(s): `runner-check`, `runner-fixed`.

The peak flow value could not be located in the parsed RPT output. Most common cause: the `--node` argument does not resolve to a known `[OUTFALLS]` or `[JUNCTIONS]` entry, so the parser falls through. Always verify the node argument before running. Related skills: `swmm-runner`, `swmm-experiment-audit`.

Recall this section via `recall_memory("peak_flow_parse_missing")` or by searching with `recall_memory_search`.

## comparison_mismatch

Observed in 0 run(s) so far. Placeholder section seeded by Memory PRD M1 so that `recall_memory("comparison_mismatch")` returns a Markdown lesson even before the first audited comparison miss surfaces. Update this section once a real comparison-mismatch run lands in `runs/`.

Recall this section via `recall_memory("comparison_mismatch")` or by searching with `recall_memory_search`.
