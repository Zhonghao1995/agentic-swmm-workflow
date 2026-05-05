# Modeling Memory Index

Generated at UTC: `2026-05-05T11:17:07+00:00`

| Run | Case | Workflow | QA | SWMM RC | Comparison | Warnings | Failure patterns | Evidence boundary |
|---|---|---|---|---:|---|---|---|---|
| codex-check-peakfix | Acceptance pipeline peak parser check | acceptance_step1 | pass | 0 | mismatch | The recorded Git working tree was not clean at run time.; baseline peak-flow record does not match the value parsed from its source report section.; Peak flow changed while the SWMM input hash is unchanged; check parser version, metric source, or report records. | comparison_mismatch | The recorded Git working tree was not clean at run time. |
| codex-status-20260503 | codex-status-20260503 | acceptance_step1 | pass | 0 | not_requested |  | no_detected_failure |  |
| generate-swmm-inp-raw-path | Generate_SWMM_inp INP-derived raw adapter benchmark | inp-derived raw-like modular adapter benchmark | pass | 0 | not_requested | flow_routing_continuity_error: Adapter executed successfully, but this result should not be treated as hydrologic validation. value=9.513; zero_target_node_peak: The selected node peak is useful as a parser smoke check, not as a calibration or validation metric. | no_detected_failure | flow_routing_continuity_error: Adapter executed successfully, but this result should not be treated as hydrologic validation. value=9.513; zero_target_node_peak: The selected node peak is useful as a parser smoke check, not as a calibration or validation metric. |
| tecnopolo-199401-prepared | Tecnopolo January 1994 prepared-input benchmark | external multi-subcatchment prepared-input benchmark | pass | 0 | not_requested | The recorded Git working tree was not clean at run time. | no_detected_failure | The recorded Git working tree was not clean at run time. |
| tuflow-swmm-module03-raw-path | tuflow-swmm-module03-raw-path | TUFLOW SWMM Module 03 full multi-raingage raw GeoPackage adapter benchmark | pass | 0 | not_requested |  | no_detected_failure |  |
| tecnopolo-199401-prepared | Tecnopolo January 1994 prepared-input benchmark | external multi-subcatchment prepared-input end-to-end | pass | 0 | not_requested | The recorded Git working tree was not clean at run time. | no_detected_failure | The recorded Git working tree was not clean at run time. |
| runner-fixed | runner-fixed | external multi-subcatchment inp month benchmark | pass |  | not_requested |  | continuity_parse_missing, missing_inp, partial_run, peak_flow_parse_missing |  |
| runner-check | runner-check | external benchmark inp run | pass |  | not_requested |  | continuity_parse_missing, missing_inp, partial_run, peak_flow_parse_missing |  |
| real-todcreek-minimal | real-todcreek-minimal | minimal real-data fallback | pass |  | not_requested |  | continuity_parse_missing, partial_run |  |
