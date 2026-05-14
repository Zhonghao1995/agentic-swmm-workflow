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
