---
type: memory-index
generated_at_utc: 2026-05-14T04:48:13+00:00
---

# Modeling memory MOC

Navigation index over the audited history that informed `lessons_learned.md` and `skill_update_proposals.md`. Wikilinks point at the underlying audit notes.

## By failure pattern

| failure_pattern | runs | audit notes |
| --- | --- | --- |
| continuity_parse_missing | 5 | [[real-todcreek-minimal]] [[runner-check]] [[runner-fixed]] [[tecnopolo-lid-placement-smoke]] [[tecnopolo-mc-uncertainty-smoke]] |
| missing_inp | 2 | [[runner-check]] [[runner-fixed]] |
| no_detected_failure | 8 | [[codex-check-peakfix]] [[codex-ci-audit-local]] [[codex-status-20260503]] [[generate-swmm-inp-raw-path]] [[tecnopolo-199401-prepared]] [[todcreek-qgis-entropy-subcatchment-20260507]] [[tuflow-swmm-module03-raw-path]] |
| partial_run | 5 | [[real-todcreek-minimal]] [[runner-check]] [[runner-fixed]] [[tecnopolo-lid-placement-smoke]] [[tecnopolo-mc-uncertainty-smoke]] |
| peak_flow_parse_missing | 2 | [[runner-check]] [[runner-fixed]] |

## By skill impact

| skill | pattern count | patterns |
| --- | --- | --- |
| swmm-builder | 1 | `missing_inp` |
| swmm-end-to-end | 2 | `missing_inp` `partial_run` |
| swmm-experiment-audit | 3 | `continuity_parse_missing` `partial_run` `peak_flow_parse_missing` |
| swmm-runner | 2 | `continuity_parse_missing` `peak_flow_parse_missing` |
