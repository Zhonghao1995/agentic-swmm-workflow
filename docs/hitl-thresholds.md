---
schema_version: 1
thresholds:
  continuity_error_over_threshold:
    severity: block
    measured_key: "continuity.flow_routing"
    operator: ">"
    value: 5.0
    evidence_path: "06_qa/qa_summary.json"
    message: "Flow routing continuity error exceeds 5% — likely solver instability."
    rationale: "Guards against numerical mass-balance failure in the dynamic-wave solver. EPA SWMM 5 Reference Manual treats continuity error below 1% as acceptable and above 10% as evidence of fundamental network or routing problems; a 5% block threshold flags the middle band where the result is plausibly usable but warrants expert inspection of solver step size, conduit geometry, and surcharge handling before promotion. Long-duration runs with open boundary conditions or intentional flood storage may legitimately approach this level — exempt via `aiswmm thresholds override <run_dir> continuity_error_over_threshold <value>` with a one-line evidence note in the run audit."
  peak_flow_deviation_over_threshold:
    severity: block
    measured_key: "peak.deviation_percent"
    operator: ">"
    value: 25.0
    evidence_path: "06_qa/qa_summary.json"
    message: "Peak flow deviation against baseline exceeds 25%."
    rationale: "Guards against silent regression between a candidate run and its declared baseline (typically the previous canonical run). 25% is a screening tolerance for structural change in network, parameters, or rainfall input — calibrated production runs should match observed peaks within roughly 10–15% (Moriasi 2015 satisfactory tier for peak flow). The threshold does not condemn the run; it forces the modeller to declare whether the deviation is the intended answer (LID retrofit, climate scenario, urbanisation scenario) or unintended drift, via `aiswmm thresholds override <run_dir> peak_flow_deviation_over_threshold <value>` with a scenario tag in the human_decisions record."
  pour_point_suspect:
    severity: warn
    measured_key: "pour_point.suspect"
    operator: "=="
    value: true
    evidence_path: "06_qa/qa_summary.json"
    message: "Pour point flagged as hydrologically suspect by GIS QA."
    rationale: "Guards against assigning the basin outlet to a cell whose DEM-derived slope direction does not converge with the upstream flow accumulation — usually indicating an offset of one or two cells onto a ridge, levee, or DEM artefact. The suspicion flag is a heuristic from the swmm-gis pipeline and is prone to false positives on flat urban catchments and low-relief coastal terrain where DEM noise dominates true gradient. The pattern fires `warn` rather than `block` so that the agent surfaces the geometry for inspection without halting the workflow; confirm via `aiswmm pour_point confirm <case_id>` after visual inspection of the pour point against the routed network in QGIS."
  calibration_nse_low:
    severity: block
    measured_key: "calibration.nse"
    operator: "<"
    value: 0.5
    evidence_path: "06_qa/qa_summary.json"
    message: "Calibration Nash-Sutcliffe Efficiency below 0.5 — calibration likely unusable."
    rationale: "Guards against shipping a calibration that performs no better than predicting the observed mean. Moriasi 2015 streamflow tiers: NSE > 0.5 satisfactory, > 0.7 good, > 0.8 very good. 0.5 is a hard screening floor — below this the model captures less variance than the long-term average and the calibrated parameters are not informative. Urban stormwater calibrations against sparse or noisy gauge records may legitimately struggle to reach NSE > 0.7; pair this metric with KGE (next threshold) since NSE alone over-penalises timing errors common in event-scale runoff, and reflect domain-specific targets in `09_audit/calibration_summary.json` before publication."
  calibration_kge_low:
    severity: block
    measured_key: "calibration.kge"
    operator: "<"
    value: 0.5
    evidence_path: "06_qa/calibration_summary.json"
    message: "Calibration Kling-Gupta Efficiency below 0.5 — calibration likely unusable."
    rationale: "Guards against the same 'no better than the mean' failure mode as NSE but using the Gupta et al. 2009 / Kling et al. 2012 decomposition into correlation r, variability ratio α, and bias ratio β. KGE > 0.5 means the model jointly beats the mean on all three components. When the threshold fires, inspect r / α / β separately in `calibration_summary.json`: low r indicates timing or shape problems (rainfall lag, routing storage); α far from 1 indicates variance mis-match (often hydrograph attenuation); β far from 1 indicates systematic over- or under-prediction (often imperviousness or infiltration parameterisation). For event-scale storm runoff KGE > 0.75 is a reasonable production target; for long-term water balance KGE > 0.85 is achievable on well-instrumented sites. Override per study via `aiswmm thresholds override`."
  calibration_pbias_high:
    severity: warn
    measured_key: "calibration.pbias_pct_abs"
    operator: ">"
    value: 30.0
    evidence_path: "06_qa/calibration_summary.json"
    message: "Absolute percent bias |PBIAS| exceeds 30% — systematic over/under-prediction."
    rationale: "Guards against systematic volumetric bias. Moriasi 2015 streamflow tiers: |PBIAS| < 5% very good, 5–10% good, 10–15% satisfactory, > 15% unsatisfactory. The 30% warn threshold is intentionally loose because aiswmm primarily targets stormwater event modelling — short event records and combined-sewer dynamics legitimately produce higher PBIAS noise than the annual streamflow water balance that the Moriasi tiers were calibrated for. Water-balance studies (LID retrofit volume accounting, climate scenario annualised volumes) should tighten to |PBIAS| < 15% via `aiswmm thresholds override <run_dir> calibration_pbias_high <value>`."
  sobol_first_order_dominant:
    severity: warn
    measured_key: "sensitivity.sobol.S_i_max"
    operator: ">"
    value: 0.8
    evidence_path: "09_audit/sensitivity_indices.json"
    message: "Single Sobol' first-order index S_i exceeds 0.8 — one parameter dominates the variance, possible structural issue."
    rationale: "Guards against publishing a sensitivity analysis whose result is structurally trivial. First-order Sobol' > 0.8 means a single parameter explains more than 80% of output variance — for the target metric, the model behaves like a one-parameter model and the remaining parameters in the calibration vector are decorative. Two diagnoses: (a) the metric only responds to one process (e.g. peak flow in a dry-weather event responds almost entirely to impervious fraction) — narrow the calibration parameter set and document the choice; (b) prior space is too narrow on the other parameters — broaden priors and re-run SA before calibration. Record the diagnosis in `runs/<run_id>/09_audit/sensitivity_notes.md` before promoting the run."
---

# HITL Thresholds

This document defines the QA patterns that trigger `request_expert_review`
in the aiswmm agent runtime. The YAML front-matter above is the source
of truth: each entry pairs a measured QA key with a comparison operator,
a numeric threshold, an evidence path the modeller can inspect, a short
operator-facing message, and a hydrology rationale.

Every threshold above has a populated `rationale` (literature-grounded,
authored 2026-05-14 by the project hydrologist). The aiswmm runtime
fires `request_expert_review` when a pattern matches and shows the
operator-facing `message` plus the rationale paragraph on stderr; any
threshold whose `rationale` regresses to a `<!-- HYDROLOGY-TODO -->`
placeholder will re-trigger the "not yet scientifically justified"
warning until a hydrologist replaces it.

## How to fill in a rationale

Replace the placeholder string with a short paragraph that answers:

1. What hydrologic phenomenon does this threshold guard against?
2. Where does the numeric value come from (literature, calibration
   round, site convention)?
3. What kinds of cases are exempt, and how should the modeller signal
   the exemption (e.g., via `aiswmm thresholds override <run_dir>`)?

## Adding a new threshold

Each new entry needs the same six keys:

| Key | Type | Purpose |
|---|---|---|
| `severity` | `warn` or `block` | Whether the pattern is informational or scientifically consequential. |
| `measured_key` | dotted path | Where to find the value inside `06_qa/qa_summary.json` (or another QA artefact). |
| `operator` | `>`, `<`, `>=`, `<=`, `==`, `!=` | The comparison applied to `measured_key` against `value`. |
| `value` | scalar | The threshold the hit is measured against. |
| `evidence_path` | relative path under the run dir | The artefact the modeller will inspect when deciding. |
| `message` | one-line string | What the operator sees on stderr when the pattern fires. |
| `rationale` | paragraph | Hydrology justification — must be filled before treating the threshold as scientifically defensible. |

The QA report key path uses dotted notation (`continuity.flow_routing`).
Missing keys are silently skipped — a partial QA report does not crash
the evaluator.

## Operator-only commands

The four CLI subcommands that make the human-authority decisions on a
run are deliberately not registered as agent tools:

- `aiswmm calibration accept <run_dir>` — promote a calibration into the canonical run.
- `aiswmm pour_point confirm <case_id>` — confirm a flagged pour point is hydrologically reasonable.
- `aiswmm thresholds override <run_dir> <name> <value>` — record a one-off override of a threshold value for a specific run.
- `aiswmm publish <run_dir>` — mark a run as publication-ready.

Each appends a `human_decisions` record to the run's
`09_audit/experiment_provenance.json`.
