# Framework gaps backlog — (B) phase to-do list

Consolidated from:
- this run's `cold_start_diagnostic.json` (15 gaps)
- baseline's `framework_mcp_manifest.json` → `framework_gaps` (4 entries) and `missing_or_fallback_inputs` (6 entries)
- environment preflight findings

Ordered by severity, then by where it lives in the chain.

---

## Blocking (cold-start agent cannot proceed without manual intervention)

### B1. Document or automate the `subcatchments.geojson` pre-step
**Where:** `skills/swmm-end-to-end/SKILL.md` Mode 0, possibly a new MCP tool in `mcp/swmm-gis`.
**Why:** Mode 0 step 1 demands a subcatchments geojson with `subcatchment_id`. Saanich's raw `DrainageBasinBoundary.shp` does not provide this. There is no MCP tool that derives subcatchments from a raw basin layer. Baseline solved this manually (picked `OBJECTID=100`, set `subcatchment_id="S1"`).
**Acceptance:** SKILL.md describes the contract; either a tool exists or the manual convention is canonical and reproducible from a documented script.

### B2. Provide `mapping.json` template for `import_city_network`
**Where:** `skills/swmm-network/templates/city_mapping.template.json` and link from SKILL.md.
**Why:** `import_city_network` requires `mappingPath` but no template, example, or schema reference exists in `skills/swmm-network/`. Baseline hand-authored a 60-line config inline.
**Acceptance:** A template file exists with commented field defaults; cold agent can copy + edit it without reading the adapter source.

### B3. Add MCP tool for outfall inference
**Where:** `mcp/swmm-network` or `mcp/swmm-gis`. Suggested name: `infer_outfall`.
**Why:** Baseline's `outfalls.geojson` was produced by an ad-hoc "endpoint of pipe nearest watercourse" heuristic, not via any MCP tool. `import_city_network` does not infer outfalls; without the file, the call fails or produces a degenerate network.
**Acceptance:** Tool takes pipes geojson + watercourse geojson and emits outfalls geojson; documented in SKILL.md step 4 prerequisites.

### B4. Install missing MCP server `node_modules`
**Where:** `mcp/swmm-calibration/`, `mcp/swmm-params/` + `scripts/install_mcp_deps.sh`.
**Why:** Both servers have no `node_modules` directory locally. They are listed in `swmm-end-to-end` SKILL.md orchestration order. Any cold-start agent that goes past Mode 0 will fail.
**Acceptance:** `node_modules/` present for all 8 servers; install script committed and referenced from SKILL.md preflight section.

---

## Friction (cold agent can proceed but would silently produce wrong output or take many wrong turns)

### F1. Surface `unmatched_landuse_classes` as a structured warning
**Where:** `mcp/swmm-gis/server.js` (`qgis_area_weighted_params`) and the params lookup CSV.
**Why:** Baseline + this run both report `Industrial` and `Single Family` zoning classes silently falling through to DEFAULT. Cold agent might not notice unless inspecting the response carefully.
**Acceptance:** Tool returns a top-level `warnings` array; the harness or Mode 0 chain promotes them into `framework_mcp_manifest.missing_or_fallback_inputs` automatically. Extend lookup CSV to cover common municipal zoning classes.

### F2. Document field-name defaults for `qgis_area_weighted_params`
**Where:** `mcp/swmm-gis/server.js` `inputSchema` + `skills/swmm-end-to-end/SKILL.md` step 1.
**Why:** `idField`, `landuseField`, `soilField` had to be reverse-engineered from baseline.
**Acceptance:** `inputSchema` has `.default("subcatchment_id")` etc., or SKILL.md gives an example call.

### F3. Document or replace manual rainfall preprocessing
**Where:** `mcp/swmm-climate` (new tool) and `skills/swmm-climate/SKILL.md`.
**Why:** The `.dat → daily CSV → event-window CSV` chain is manual. `format_rainfall` does have `inputDatPaths`, but it is undocumented in SKILL.md.
**Acceptance:** Either Mode 0 explicitly calls `format_rainfall` with `inputDatPaths` for Saanich-style inputs, or a new `select_event` tool exists.

### F4. Diameter fallback policy is silent
**Where:** `mcp/swmm-network/server.js` `import_city_network` and adapter.
**Why:** Pipes with nonnumeric `DIAMETER` get a silent `geom1=0.3 m` default. The hint is buried in `meta.diameter_policy` string of `network.json`.
**Acceptance:** Tool response includes a structured `pipe_warnings: [{id, field, raw_value, fallback}]` list; the orchestration manifest auto-includes them.

### F5. Provide `options_config.json` template for `build_inp`
**Where:** `skills/swmm-builder/templates/options_config.template.json`.
**Why:** No template or schema for SWMM `[OPTIONS]` block. Baseline hardcoded April 1984 dates.
**Acceptance:** Template exists with commented keys; SKILL.md references it.

### F6. `swmm_run` default `node="O1"` mismatches outfall naming
**Where:** `mcp/swmm-runner/server.js`.
**Why:** Saanich uses `OUT1`. Passing the `O1` default would run SWMM but break peak extraction.
**Acceptance:** Either auto-detect first outfall from `.inp [OUTFALLS]` section when `node` is omitted, or make `node` required (remove misleading default).

### F7. Add `--list-tools` flag to `mcp_stdio_call.py`
**Where:** `skills/swmm-end-to-end/scripts/mcp_stdio_call.py`.
**Why:** Currently the only way to enumerate tools is to pass an invalid name and parse the `ValueError`.
**Acceptance:** `--list-tools` flag exits 0 after printing tool names and descriptions.

### F8. Parameterise `test_saanich_framework_smoke_manifest.py`
**Where:** `tests/test_saanich_framework_smoke_manifest.py`.
**Why:** `RUN_DIR` is hardcoded to a specific baseline timestamp; `runs/` is `.gitignored`, so the test cannot pass on a fresh clone. Each new smoke run requires editing the test or being silently ignored.
**Acceptance:** Test discovers the most recent `runs/*-saanich-framework-smoke/framework_mcp_manifest.json` (or reads `SAANICH_SMOKE_RUN_DIR` env var), and `pytest.skip`s if nothing is found.

### F9. Add explicit trigger examples to `swmm-end-to-end` description
**Where:** `skills/swmm-end-to-end/SKILL.md` frontmatter and "When to use".
**Why:** Generic description ("Top-level orchestration skill for OpenClaw-driven SWMM modelling") competes with `swmm-builder`, `swmm-gis`, `swmm-rag-memory` for the same prompts.
**Acceptance:** 2–3 verbatim example prompts under description; "Do not use" section with disambiguation.

### F10 (carry-over). `runner_metric_json_tools` — add `outputPath` to `swmm_continuity` and `swmm_peak`
**Where:** `mcp/swmm-runner/server.js`.
**Why:** Continuity and peak responses are captured only as raw MCP responses; the manifest references them via the response file. Dedicated `outputPath` support would make stage artifacts cleaner and let manifest.outputs list the metric files directly.
**Acceptance:** Both tools take an optional `outputPath`; when provided, write the structured JSON there and reference it.

---

## Minor (polish, not blocking)

### M1. `pyswmm` import OOM-kills on probe
**Where:** `skills/swmm-runner` or end-to-end preflight.
**Why:** Probing `import pyswmm` triggered SIGKILL 137. Nothing in Mode 0 needs it but it is implied dependency. Cold agent might not notice the silent failure.
**Acceptance:** Either remove the dependency, or add a preflight that skips it explicitly with a note.

### M2. Add deterministic event-window selection tool
**Where:** `mcp/swmm-climate select_event` (related to F3).
**Why:** April 9–13 1984 was hand-picked. No documented way to pick deterministically from a daily series.
**Acceptance:** Tool returns top-N event windows by cumulative depth; chosen window recorded in manifest.

---

## Carry-over from baseline `framework_gaps` and `missing_or_fallback_inputs`

The 4 entries in baseline `framework_gaps` map cleanly to the entries above:
- `saanich_diameter_fallback` → **F4**
- `saanich_landuse_lookup_coverage` → **F1**
- `runner_metric_json_tools` → **F10**
- `soil_absence_policy` → (new) **F11**: end-to-end skill should require explicit `soil_layer_missing` declaration when no soil geojson exists; currently the operator/agent is responsible for remembering to add it to `missing_or_fallback_inputs`.

The 6 `missing_or_fallback_inputs` are not separate gaps to fix — they are **scientific-quality** fallbacks specific to Saanich. They will be addressed in the per-fallback (B) phase if/when the user wants to upgrade the smoke into a quality-grade model. They are:
- `soil_layer_missing` (link: B3 outfall infer is related; also F11 policy)
- `diameter_non_numeric` (link: F4)
- `outfall_layer_missing` (link: B3)
- `invert_elevation_missing` (no current MCP path; needs DEM-based invert inference)
- `rainfall_shortened` (link: F3 / M2)
- `slope_missing` (no current MCP path; needs DEM-based slope tool — relates to `swmm-gis/qgis_extract_slope_area_width` which exists but was not used)

---

## Suggested execution order for (B) phase

1. **B4** first — without `node_modules` you cannot test new MCP work in `swmm-params`/`swmm-calibration` regardless of priority.
2. **B1** + **B2** + **B3** — these unblock cold-start agents on Saanich-style raw inputs. After these, a cold agent should be able to produce `subcatchments.geojson`, `mapping.json`, `outfalls.geojson` without manual help.
3. **F1**, **F4**, **F10**, **F11** — propagate fallbacks through structured channels rather than free-text strings.
4. **F2**, **F5**, **F6**, **F9** — DX polish (defaults, templates, trigger examples).
5. **F3**, **M2** — rainfall side.
6. **F7**, **F8** — testing/observability polish.
7. **M1** — pyswmm.
