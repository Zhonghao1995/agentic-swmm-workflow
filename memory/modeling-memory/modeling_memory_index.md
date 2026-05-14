# Modeling Memory Index

Generated at UTC: `2026-05-07T00:00:00-07:00`

| Run | Project | Case | Workflow | QA | SWMM RC | Comparison | Warnings | Failure patterns | Model diagnostics | Evidence boundary |
|---|---|---|---|---:|---|---|---|---|---|---|
| codex-check-peakfix | acceptance | codex-check-peakfix | acceptance_step1 | pass | 0 | not_requested | The recorded Git working tree was not clean at run time. | no_detected_failure |  | The recorded Git working tree was not clean at run time. |
| codex-ci-audit-local | acceptance | codex-ci-audit-local | acceptance_step1 | pass | 0 | not_requested | The recorded Git working tree was not clean at run time. | no_detected_failure |  | The recorded Git working tree was not clean at run time. |
| codex-status-20260503 | acceptance | codex-status-20260503 | acceptance_step1 | pass | 0 | not_requested |  | no_detected_failure |  |  |
| generate-swmm-inp-raw-path | generate-swmm-inp | Generate_SWMM_inp INP-derived raw adapter benchmark | inp-derived raw-like modular adapter benchmark | pass | 0 | not_requested | flow_routing_continuity_error: Adapter executed successfully, but this result should not be treated as hydrologic validation. value=9.513; zero_target_node_peak: The selected node peak is useful as a parser smoke check, not as a calibration or validation metric. | no_detected_failure | conduit_slope_suspicious, continuity_error_high | flow_routing_continuity_error: Adapter executed successfully, but this result should not be treated as hydrologic validation. value=9.513; zero_target_node_peak: The selected node peak is useful as a parser smoke check, not as a calibration or validation metric. |
| tecnopolo-199401-prepared | tecnopolo | Tecnopolo January 1994 prepared-input benchmark | external multi-subcatchment prepared-input benchmark | pass | 0 | not_requested | The recorded Git working tree was not clean at run time. | no_detected_failure |  | The recorded Git working tree was not clean at run time. |
| tecnopolo-lid-placement-smoke | tecnopolo | tecnopolo-lid-placement-smoke | unknown | pass |  | not_requested |  | continuity_parse_missing, partial_run |  |  |
| tecnopolo-mc-uncertainty-smoke | tecnopolo | Tecnopolo prior Monte Carlo uncertainty smoke | prior Monte Carlo uncertainty smoke for prepared-input SWMM | pass |  | not_requested |  | continuity_parse_missing, partial_run |  |  |
| tuflow-swmm-module03-raw-path | tuflow | tuflow-swmm-module03-raw-path | unknown | pass | 0 | not_requested |  | no_detected_failure |  |  |
| tecnopolo-199401-prepared | tecnopolo | Tecnopolo January 1994 prepared-input benchmark | external multi-subcatchment prepared-input end-to-end | pass | 0 | not_requested | The recorded Git working tree was not clean at run time. | no_detected_failure |  | The recorded Git working tree was not clean at run time. |
| runner-fixed | tecnopolo | runner-fixed | unknown | pass |  | not_requested |  | continuity_parse_missing, missing_inp, partial_run, peak_flow_parse_missing |  |  |
| runner-check | tecnopolo | runner-check | unknown | pass |  | not_requested |  | continuity_parse_missing, missing_inp, partial_run, peak_flow_parse_missing |  |  |
| real-todcreek-minimal | tod-creek | real-todcreek-minimal | unknown | pass |  | not_requested |  | continuity_parse_missing, partial_run |  |  |
| todcreek-qgis-entropy-subcatchment-20260507 | tod-creek | Tod Creek QGIS entropy-guided subcatchment partition | qgis_entropy_subcatchment_partition | pass | n/a | not_requested | GIS preprocessing only; not calibrated SWMM validation. | no_detected_failure | not_applicable_gis_preprocessing | Raw GIS to entropy-guided SWMM subcatchment spatial-unit selection; not hydrologic validation. |
