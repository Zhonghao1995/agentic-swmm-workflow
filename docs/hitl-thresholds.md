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
    rationale: "<!-- HYDROLOGY-TODO: hydrologist (you) writes the rationale here. Default 5% reflects EPA-SWMM continuity advice; revise once site-specific tolerance is established. -->"
  peak_flow_deviation_over_threshold:
    severity: block
    measured_key: "peak.deviation_percent"
    operator: ">"
    value: 25.0
    evidence_path: "06_qa/qa_summary.json"
    message: "Peak flow deviation against baseline exceeds 25%."
    rationale: "<!-- HYDROLOGY-TODO: peak deviation tolerance depends on storm design and study purpose (calibration vs. screening). Document the choice before promoting any run to canonical. -->"
  pour_point_suspect:
    severity: warn
    measured_key: "pour_point.suspect"
    operator: "=="
    value: true
    evidence_path: "06_qa/qa_summary.json"
    message: "Pour point flagged as hydrologically suspect by GIS QA."
    rationale: "<!-- HYDROLOGY-TODO: pour-point suspicion is currently a heuristic flag from the swmm-gis pipeline. Document accepted false-positive rate and overrides per case. -->"
  calibration_nse_low:
    severity: block
    measured_key: "calibration.nse"
    operator: "<"
    value: 0.5
    evidence_path: "06_qa/qa_summary.json"
    message: "Calibration Nash-Sutcliffe Efficiency below 0.5 — calibration likely unusable."
    rationale: "<!-- HYDROLOGY-TODO: NSE threshold should reflect study domain (urban vs. rural) and observed-flow data quality. Default 0.5 is a screening floor only. -->"
---

# HITL Thresholds

This document defines the QA patterns that trigger `request_expert_review`
in the aiswmm agent runtime. The YAML front-matter above is the source
of truth: each entry pairs a measured QA key with a comparison operator,
a numeric threshold, an evidence path the modeller can inspect, a short
operator-facing message, and a hydrology rationale.

The `rationale` field on every threshold above is currently a
`<!-- HYDROLOGY-TODO -->` placeholder. The aiswmm runtime will still
function with placeholders — `request_expert_review` will fire and the
human will see a clear stderr banner — but **the system emits a warning
that the threshold has not yet been scientifically justified**. Once a
hydrologist fills in the rationale text, the warning quiets.

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
