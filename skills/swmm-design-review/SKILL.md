---
name: swmm-design-review
description: >
  Score a completed SWMM run against a configurable YAML rulebook of
  design checks — GB50014-style standards for real catchments, or the
  reference-free physical-plausibility rulebook for synthesized
  networks. Reads the run's existing manifest.json / model.rpt /
  model.inp and never re-runs SWMM. Use it post-run for compliance or
  plausibility review; continuity gating stays with postflight.
---

# swmm-design-review — Design Review / Code-Compliance Checker

## What this skill does

Evaluates a completed SWMM run against a configurable rulebook of design checks.
Reads the run's existing artifacts (manifest.json + model.rpt + model.inp) — SWMM
is never re-run. Classifies each rule as `pass`, `fail`, `warn`, or `needs-data`
and writes `11_review/design_review.json` + `11_review/design_review.md` into the
run directory (canonical per ADR-0004; the underlying script's own bare default
is the legacy `09_review/` — see *CLI usage* below).

This skill is **decision-support only**. It never certifies regulatory compliance.

---

## CLI usage

```bash
# Standalone script
python3 skills/swmm-design-review/scripts/design_review.py \
    --run-dir <path>         # required: directory with model.rpt, manifest.json, model.inp
    [--rpt <path>]           # override: explicit model.rpt path
    [--inp <path>]           # override: explicit model.inp path
    [--manifest <path>]      # override: explicit manifest.json path
    [--rules <rulebook>]     # override rulebook YAML/JSON (repeatable for multiple books)
    [--out-dir <dir>]        # bare-script default: <run-dir>/09_review/ (legacy)
    [--no-inp]               # skip INP-derived metrics (slope, diameter)

# CLI verb (registered in aiswmm CLI) — always passes --out-dir explicitly,
# defaulting to the canonical <run-dir>/11_review/ (ADR-0004)
aiswmm review --run-dir <path> [--rules <rulebook.yaml>] [--out-dir <dir>]
```

Exit codes: `0` = pass/warn/needs-data only; `1` = any FAIL; `2` = script/input error.

## Agent tool: `review_run`

Registered in `AgentToolRegistry`. Direct handler (not MCP-routed) — writes
`11_review/design_review.json` + `11_review/design_review.md` into the run dir
(canonical per ADR-0004).

```
review_run(run_dir="runs/my_run/")
review_run(run_dir="runs/my_run/", rules="skills/swmm-design-review/rulebooks/gb50014_template.yaml")
```

`is_read_only=False` — QUICK profile prompts the user (tool writes files).

## Executed example

```bash
python3 skills/swmm-design-review/scripts/design_review.py \
    --run-dir tests/fixtures/design_review \
    --manifest tests/fixtures/design_review/sample_manifest.json \
    --rpt tests/fixtures/design_review/sample_mini.rpt \
    --inp tests/fixtures/design_review/sample_mini.inp \
    --rules tests/fixtures/design_review/sample_rules.yaml \
    --out-dir /tmp/design_review_out
# Exit 1 = FAIL (1 pass, 1 fail, 0 warn, 1 needs-data)
# Report: /tmp/design_review_out/design_review.md
```

---

## Return-period adequacy workflow

To check return-period adequacy (RETURN_PERIOD_ADEQUACY rule):

1. `generate_design_storm` with the target return period P.
2. `build_inp` to integrate the design storm into the model.
3. `run_swmm_inp --storm-return-period-yr P` to propagate provenance to manifest.
4. `design_review.py --run-dir <path>` — extractor reads `manifest.metadata.storm_return_period_yr`.

Until step 3 is wired (separate PR), RETURN_PERIOD_ADEQUACY returns `needs-data`.

---

## Metrics available today (PR1)

| Metric | Source |
|---|---|
| `run.peak_flow` | manifest.json |
| `run.continuity_error_pct` | manifest.json |
| `link.max_velocity` | rpt Link Flow Summary |
| `link.max_full_flow_ratio` | rpt Link Flow Summary |
| `link.max_full_depth_ratio` | rpt Link Flow Summary |
| `link.peak_flow` | rpt Link Flow Summary |
| `outfall.max_flow` | rpt Outfall Loading Summary |
| `node.flow_balance_error_pct` | rpt Node Inflow Summary |
| `conduit.slope_pct` | INP [CONDUITS] + [JUNCTIONS]/[OUTFALLS] join |
| `conduit.diameter_m` | INP [XSECTIONS] GEOM1 (CIRCULAR only) |
| `conduit.roughness` | INP [CONDUITS] |

Metrics returning `needs-data` until PR2: `node.surcharge_hours`, `node.max_depth_m`,
`node.flooding_hours`, `node.flooding_volume_m3`, `junction.freeboard_m`,
`run.return_period_yr`.

---

## Output files

- `<run-dir>/11_review/design_review.json` — machine-readable results (schema_version 1.0)
- `<run-dir>/11_review/design_review.md` — human report with disclaimer and sign-off table

(Canonical location per ADR-0004. Runs from before that migration, or the bare
script invoked without `--out-dir`, carry these under the legacy `09_review/`.)

---

## What the agent MUST say

When presenting review results, the agent must:

1. Lead with the overall status (PASS / FAIL / WARN / NEEDS-DATA).
2. State the disclaimer: _"Findings are decision-support only and do NOT constitute
   compliance with any drainage standard."_
3. Invite the engineer to inspect any FAIL or NEEDS-DATA finding before proceeding.
4. List NEEDS-DATA rules prominently with the reason they cannot produce a result.

## What the agent MUST NOT say

- Do NOT say "the design is compliant with GB 50014" or any equivalent.
- Do NOT say "no issues found" when there are NEEDS-DATA rules — those are not passes.
- Do NOT paraphrase away the disclaimer.
- Do NOT imply the thresholds are authoritative — all template thresholds carry
  `verify: true` precisely because they must be confirmed by the user.

---

## Custom rulebooks

Write a YAML file following the schema in `rulebooks/gb50014_template.yaml`.
Every rule must have:

- `id` (SCREAMING_SNAKE_CASE, unique)
- `metric` (named extractor from the table above)
- `operator` (`lte` / `lt` / `gte` / `gt` / `eq` / `neq` / `between`)
- `threshold` (float, SI) or `threshold_low` + `threshold_high` for `between`
- `units: SI`
- `severity: FAIL` or `severity: WARN`
- `citation` — clause reference or `"TODO: cite local standard"`
- `verify: true` unless you have personally confirmed the threshold against the
  applicable edition of the standard

**Honesty rule:** a rule MUST NOT have `verify: false` while `citation` is still
`"TODO: cite local standard"`. The test suite enforces this invariant.

---

## Part of

Issue #249 capability backlog — design-review / code-compliance checker.
PR1: extractor layer + evaluator + rulebook + reports + tests.
PR2 (upcoming): ToolSpec wiring, CLI verb, rpt_summary extensions.
