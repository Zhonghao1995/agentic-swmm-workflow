# Cold-start agent diagnostic — Saanich framework smoke (20260513-030729)

> Companion narrative to `cold_start_diagnostic.json`. Captures the implicit
> knowledge or undocumented prerequisites a human-augmented operator (this
> Claude session, given the baseline run as reference) had to supply while
> running Mode 0 end-to-end. Each section is one or more candidate gaps a
> cold-start agent would hit.

**Run case:** `runs/20260513-030729-saanich-framework-smoke/`
**Baseline:** `runs/20260513-014020-saanich-framework-smoke/` (used as seed for inputs)
**Operator:** `claude-opus-4-7`
**Outcome:** 8/8 MCP calls returned `status: ok`; `model.inp` is byte-identical to baseline; peak (0.001 cms at OUT1 21:45) and continuity error (-0.109% / 0.0%) match. Framework chain after the `mcp/` top-level restructure is healthy.

---

## Step-by-step orchestration trace

### Pre-step 0 — Where does `subcatchments.geojson` come from?
**Severity:** blocking.

Mode 0 step 1 (`swmm-gis-mcp.qgis_area_weighted_params`) requires a `subcatchments` argument pointing to a geojson with a `subcatchment_id` field. The baseline kept this file at `01_gis/subcatchments.geojson` — *not* in `00_raw/`. SKILL.md does not describe how to produce it from Saanich's raw `DrainageBasinBoundary.shp` (which has 196 polygons, none with a `subcatchment_id` field).

I copied the baseline's `01_gis/subcatchments.geojson` and `subcatchments.csv` verbatim. A cold-start agent would be stuck: the canonical question "from the raw Saanich shapefiles, what is my subcatchment layer?" has no in-skill answer.

**Suggested fix:** add an explicit pre-step in Mode 0 that documents the `subcatchments.geojson` contract (field name `subcatchment_id`, projected CRS, polygon geometry) and either points at a delineation MCP tool or formalises the manual basin-selection convention used here.

### Step 1 — `swmm-gis-mcp.qgis_area_weighted_params`
**Severity:** friction (two findings).

1. The required field names `idField=subcatchment_id`, `landuseField=CLASS`, `soilField=TEXTURE` were not declared in SKILL.md. I had to read the baseline response payload and the harness test (`tests/test_mcp_stdio_framework_harness.py`) to recover them.
2. The tool returned `unmatched_landuse_classes: ["Industrial", "Single Family"]` and silently substituted a DEFAULT row in the params lookup. The lookup CSV at `skills/swmm-params/references/landuse_class_to_subcatch_params.csv` does not cover Saanich's zoning vocabulary.

**Suggested fix:** add `default` values to the `inputSchema` for the three field-name args; extend the landuse lookup CSV; promote unmatched classes into `framework_gaps` automatically.

### Step 2 — `swmm-climate-mcp.format_rainfall`
**Severity:** friction.

Saanich provides only `data/Saanich/Rainfall/1984rain.dat` (daily, 182 rows). The MCP tool supports `inputDatPaths` as an alternate input, but the baseline used a hand-derived 5-day-event CSV (`rainfall_event_avg_intensity.csv`) that was itself derived from a daily-aggregated CSV. **Neither preprocessing step is covered by any MCP tool.**

I seeded the same CSV from baseline. A cold-start agent has no documented path from raw `.dat` to call 2 input.

**Suggested fix:** either document `inputDatPaths` clearly in SKILL.md, or add an MCP tool for event-window selection (`select_event(dat_path, top_n=1, min_window_days=3)`).

### Step 3 — `swmm-network-mcp.import_city_network`
**Severity:** blocking (three findings).

1. The required `mappingPath` argument expects a fairly elaborate JSON config (pipe-field-to-SWMM defaults, junction inference rules, etc.). The baseline `mapping.json` lives in `04_network/`, was hand-authored, and is **not referenced or templated anywhere in `skills/swmm-network/`**. A cold-start agent has no example.
2. The required `outfallsGeojsonPath` argument expects a point geojson with `node_id`, `type`, `invert_elev`. The baseline `outfalls.geojson` was produced by an ad-hoc "endpoint of pipe nearest to a watercourse" heuristic. **That heuristic is not implemented in any MCP tool.**
3. Saanich's `DIAMETER` field contains nonnumeric values like `"Other"`. The mapping silently falls back to `geom1=0.3 m` for all such pipes. The fallback is recorded only as a string in `meta.diameter_policy` of `network.json` — not surfaced in the tool response.

**Suggested fixes:**
- add `skills/swmm-network/templates/city_mapping.template.json`
- add MCP tool `infer_outfall(pipes_geojson, watercourse_geojson, mode='endpoint_nearest_watercourse') -> outfalls_geojson`
- promote `diameter_policy` to a structured warning in the tool response and auto-feed it into `framework_mcp_manifest.missing_or_fallback_inputs`

### Step 4 — `swmm-network-mcp.qa`
**Severity:** none. Worked transparently with only `networkJsonPath` argument.

### Step 5 — `swmm-builder-mcp.build_inp`
**Severity:** friction.

`configJsonPath` accepts a SWMM `[OPTIONS]` block as JSON (dates, routing step, etc.). The baseline `options_config.json` was hand-authored with hardcoded `START_DATE=04/09/1984 / END_DATE=04/13/1984`. No template, no schema reference, no default.

**Suggested fix:** add `skills/swmm-builder/templates/options_config.template.json` with commented keys; document allowed values in SKILL.md.

### Step 6 — `swmm-runner-mcp.swmm_run`
**Severity:** friction.

The `node` argument defaults to `"O1"`, but Saanich's only outfall is `"OUT1"` (defined in `outfalls.geojson`). Passing the default would still run SWMM, but downstream `swmm_peak` would extract zero for the wrong node. A cold-start agent that did not override `node` would get a silently-wrong peak.

**Suggested fix:** either auto-detect the first outfall from the .inp `[OUTFALLS]` section when `node` is omitted, or remove the `"O1"` default and make `node` required.

### Steps 7–8 — `swmm-runner-mcp.swmm_continuity` and `swmm_peak`
**Severity:** none for transport; minor for output. Both worked. The previously-noted gap `runner_metric_json_tools` (raw response only, no `outputPath` arg) still applies but is not blocking.

### Test harness — `tests/test_saanich_framework_smoke_manifest.py`
**Severity:** friction.

`RUN_DIR` is hardcoded to `runs/20260513-014020-saanich-framework-smoke`. The `runs/` directory is `.gitignored`. So this test:
- only passes locally for whoever made the baseline run;
- silently fails for anyone else who clones the repo;
- doesn't update when a fresh smoke run is produced.

**Suggested fix:** parameterise `RUN_DIR` (env var `SAANICH_SMOKE_RUN_DIR` or discover most-recent matching `runs/*-saanich-framework-smoke`), and gate the test with `pytest.skip` if no such run exists.

### Environment preflight — missing `node_modules`
**Severity:** blocking-for-other-modes.

`mcp/swmm-calibration/node_modules` and `mcp/swmm-params/node_modules` are not installed. These two servers are not on the Mode 0 critical path, but `swmm-end-to-end` SKILL.md lists `swmm-params` and `swmm-calibration` as part of the orchestration order. Any cold-start agent that follows SKILL.md past Mode 0 will fail at first contact.

**Suggested fix:** ship `scripts/install_mcp_deps.sh` that runs `npm install` in every `mcp/*/`; reference it in SKILL.md preflight.

### Skill triggering
**Severity:** friction.

The natural-language prompt "用 Saanich 数据测试是否能调用 QGIS 和 SWMM 的组装 inp 文件能力" correctly routed to `swmm-end-to-end` via the description keywords. But the description is generic ("Top-level orchestration skill for OpenClaw-driven SWMM modelling"). A cold-start agent could plausibly route the same prompt to `swmm-builder` (which directly mentions "inp"), `swmm-gis` (which mentions "QGIS"), or `swmm-rag-memory`.

**Suggested fix:** add 2–3 verbatim example trigger prompts to the SKILL.md description and a "Do not use" section pointing to confusable siblings.

---

## Summary signal for (B) phase

- **Smoke transport is healthy** (8/8 ok, bit-identical to baseline) — the `mcp/` top-level restructure did not break Mode 0.
- **15 cold-start gaps recorded** in `cold_start_diagnostic.json`. Severity breakdown:
  - blocking: 4 (subcatchment input, mapping.json template, outfall inference, missing node_modules)
  - friction: 9
  - minor: 2
- See `framework_gaps_backlog.md` (sibling file) for the consolidated to-do list, ordered by severity.
