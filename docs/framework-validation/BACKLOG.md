# Framework gaps — canonical backlog

Single source of truth for known gaps in the Agentic SWMM skill+MCP
framework, ordered by severity and consolidated from:

- **Operator-driven smoke** — `saanich-smoke-20260513/` (re-ran Mode 0
  with baseline-seeded `00_raw/`; surfaced 15 gaps in
  `cold_start_diagnostic.json`).
- **Cold-start agent run** — `saanich-cold-start-cecelia-20260513/`
  (fresh agent, only natural-language prompt, no access to prior runs
  or this BACKLOG; surfaced 12 gaps, 5 of which the operator-run did
  not see).

Run-specific files in each sub-directory remain frozen as historical
evidence. **This file is the living document.** Update here when a gap
is closed or a new one is discovered.

Status legend: `open`, `in_progress`, `done`.

---

## Blocking (cold-start agent cannot proceed without writing custom code or fabricating values)

### B1 — `subcatchments.geojson` source pipeline
**Status:** **done** (commit `31d6549`).
**Resolution:** shipped `mcp/swmm-gis basin_shp_to_subcatchments` —
takes a basin shapefile and emits subcatchments.geojson +
subcatchments.csv (subcatchment_id, outlet, area_ha, width_m,
slope_pct, rain_gage). Four selection modes: by_id_field (default),
by_index, largest, all. Width = sqrt(area_m2), slope = 1.0% by default
(both parameterised). Verified on a synthetic 3-polygon basin + a real
Saanich Colquitz selection.

### B2 — `mapping.json` template for `import_city_network`
**Status:** **done.**
**From:** operator.
**The gap:** `import_city_network` requires `mappingPath` but the
`skills/swmm-network/` directory had no documented template for the
"raw municipal shapefile" shape (the structured-export shape was
already covered by `examples/city-dual-system/mapping.json`).
**Resolution:** added `skills/swmm-network/templates/`:
- `city_mapping_raw_shapefile.template.json` — fully-specified mapping
  for Saanich-style raw `StormGravityMain.shp` (LineString pipes only;
  adapter infers junctions from endpoints).
- `README.md` — decision walkthrough explaining when to use each
  shape (structured CAD vs raw shapefile), field-by-field meaning,
  and which orthogonal gaps (B3 outfall, B5 reorientation) remain
  outside the mapping config.
- `skills/swmm-network/SKILL.md` extended with a template-picking
  table.
- `skills/swmm-end-to-end/SKILL.md` Mode 0 step 4 + step 5 updated
  with explicit pointers to the templates and the expected B3/B5
  workarounds.
**Verification:** ran `import_city_network` with the new template
against the Saanich baseline pipes/outfalls geojson — output counts
match the baseline byte-for-byte (7 pipes, 0 explicit junctions, 1
outfall, 9 inferred junctions).

### B3 — Outfall inference MCP tool
**Status:** **done** (commit `f9ad452`).
**Resolution:** shipped `mcp/swmm-network infer_outfall` with two
modes: `endpoint_nearest_watercourse` (default, needs watercourse
geojson) and `lowest_endpoint` (uses min-y, no watercourse needed).
Emits a single-Point outfalls.geojson with node_id=OUT1, type=FREE,
plus provenance properties (source_pipe, source_position,
dist_to_watercourse_m). On the Saanich Colquitz E2E run it picked
DGM004947 end at 0.0 m to watercourse — the same outfall the operator
hand-derived in the baseline.

### B4 — Install missing MCP `node_modules`
**Status:** **done** (commit `49f6d04` + `2d06404`).
**From:** operator preflight.
**Resolution:** `scripts/install_mcp_deps.sh` ships in repo; loops every
`mcp/*/package.json` and runs `npm install`. SKILL.md `Preflight`
section documents the command. All 8 servers now respond to
`tools/list`.

### B5 — Pipe orientation = flow direction
**Status:** **done** (commit `944d2ef`).
**Resolution:** shipped a standalone `mcp/swmm-network reorient_pipes`
tool that runs BFS from the outfall vertices and flips LineString
direction where it doesn't match flow. Output: pipes_oriented.geojson
plus a structured report with `pipes_reversed` / `pipes_unreached`
counts and the indices of touched features. Kept separate from
`import_city_network` so the heuristic stays swappable.

### B7 — Subcatchment outlet assignment to a real network node
**Status:** **done** (PR-merging branch `b7-assign-subcatchment-outlets`).
**From:** discovered after the Phase 1 lock-in, by inspecting the
post-E2E .rpt and noticing every junction had 0.000 lateral inflow.
**The gap:** `basin_shp_to_subcatchments` (B1) writes the literal
outfall (`OUT1`) as every subcatchment's `outlet`. This means the SWMM
model's pipe network is built but never receives surface runoff —
water flows subcatchment → outfall directly, bypassing all junctions
and conduits. The pipe network is decorative.
**Resolution:** shipped `mcp/swmm-network assign_subcatchment_outlets`.
Three modes:
- `nearest_junction` (default): centroid → closest junction in
  `network.json` (junctions list; outfalls excluded by default but
  optionally includable).
- `nearest_catch_basin`: same but uses a separate manhole/catch-basin
  GeoJSON, so a richer Saanich-style `StormManhole.shp` can override
  the inferred junctions.
- `manual_lookup`: 2-column CSV `subcatchment_id,outlet_node_id` for
  agent / human override.
Output: rewritten subcatchments CSV with the `outlet` column updated.
Three pytest cases cover all modes. Verified end-to-end on Saanich
Colquitz: junction `J_AUTO_473439p647_5369218p478` (23.7 m from S1
centroid) now receives 0.001 cms / 0.369 ML — the runoff actually
enters the pipe network. See `saanich-b7-network-routed-20260513/`.

### B8 — Pipe network vertex snapping / connectivity healing
**Status:** **done.**
**Resolution:** shipped `mcp/swmm-network snap_pipe_endpoints`.
Union-find clusters every pipe endpoint within `tolerance_m` and
snaps each cluster to its centroid; rewrites the LineStrings so
adjacent pipes share identical endpoint coordinates. Also drops any
pipe whose two endpoints land in the same cluster (would be a
self-loop conduit that SWMM and `import_city_network` both reject).
Reports per-cluster max snap distance and the dropped-self-loop list.
Five pytest cases cover the algorithm; verified end-to-end on Saanich
Colquitz where it merged 4 endpoint clusters at max 2.08 m and
dropped 1 self-loop pipe (DGM023798). See
`saanich-b8-end-to-end-out1-flowing-20260513/`.

**Honest limit observed in the lock-in:** B8 cannot conjure missing
pipes. After snapping, 3 of Saanich's 6 remaining pipes are still
graph-disconnected from OUT1 because the basin polygon
(`OBJECTID=100`) clips out a trunk sewer that physically connects
the two sub-graphs in the wider municipal network. A future
"buffered basin clip" feature in `prepare_storm_inputs` would close
that gap; not yet a BACKLOG entry.

### B6 — Saanich-shape → city_network_adapter CSV adapter
**Status:** **done** (commit `88d9d6a`).
**Resolution:** shipped `mcp/swmm-network prepare_storm_inputs`. Takes
pipe shapefile (+ optional manhole shapefile), a basin clip geojson,
and a mapping template path. Clips both layers to the basin, validates
CRS consistency, copies + fills the mapping template with case-specific
meta fields, and emits pipes.geojson, manholes.geojson, mapping.json.
Kept narrow: outfall inference (B3) and pipe reorientation (B5) stay
in their own tools so heuristics remain swappable. Verified on
synthetic shp + Saanich Colquitz: 7 pipes / 4 manholes clipped,
matches the hand-authored baseline.

---

## Friction (proceeds but with silent wrong defaults, undocumented
fields, or many wrong turns)

### F1 — Surface `unmatched_landuse_classes` as structured warning
**Status:** **done** (commit `8f266f2`).
**Resolution:** (a) extended
`skills/swmm-params/references/landuse_class_to_subcatch_params.csv`
with 7 new common municipal zoning classes (Park, Agricultural, Single
Family, Multi-Family, Residential generic, Industrial, Institutional,
Mixed Use). Saanich's Zoning.shp now resolves cleanly without DEFAULT.
(b) `area_weighted_swmm_params.py` now emits a top-level `warnings`
array (`{code, value, fallback_class, fallback_area_m2}`) so a manifest
writer can promote unmatched classes automatically into
`missing_or_fallback_inputs` with area scale attached.

### F2 — Document field-name defaults for `qgis_area_weighted_params`
**Status:** open. **From:** operator.

### F3 — Document or replace manual rainfall preprocessing
**Status:** open. **From:** operator + cold-start agent.
**Cold-start addition:** the `.dat` file header `;Rainfall (mm)` must
be inspected manually to infer `mm_per_day`. SKILL.md should state
explicitly that Saanich-style `.dat` is `mm_per_day` (or have a probe
tool).

### F4 — Diameter fallback policy is silent
**Status:** open. **From:** operator.

### F5 — `options_config.json` template
**Status:** open. **From:** operator + cold-start agent.

### F6 — `swmm_run` default `node="O1"` mismatches outfall naming
**Status:** **done** (commit `23cffed`).
**Resolution:** `swmm_run`'s `node` arg is now optional. When omitted,
the server parses the .inp `[OUTFALLS]` section and uses the first
entry as the target node for the manifest's peak metric. `swmm_peak`'s
`node` arg is now required (no default), since `swmm_peak` only sees
the .rpt and cannot auto-detect. The misleading `"O1"` default is gone.

### F7 — `--list-tools` flag for `mcp_stdio_call.py`
**Status:** open. **From:** operator + cold-start agent.

### F8 — Parameterise `test_saanich_framework_smoke_manifest.py`
**Status:** open. **From:** operator.

### F9 — Trigger examples in `swmm-end-to-end` description
**Status:** open. **From:** operator. (Cold-start agent did pick the
right skill, so this is friction not blocking, but two correct routings
don't refute the underlying ambiguity.)

### F10 — `outputPath` argument for `swmm_continuity` / `swmm_peak`
**Status:** open. **From:** operator (`runner_metric_json_tools`).

### F11 — Soil-absence declaration policy
**Status:** open. **From:** operator.

### F12 — Invert-elevation inference
**Status:** open (new from cold-start agent).
**The gap:** Saanich storm pipes have no Z values. The cold-start
agent synthesised inverts from y-coordinate ranking (a pure
fabrication); the operator's baseline used invert_elev=0 for all
junctions. Neither is documented. No MCP tool infers inverts from a
DEM or from pipe-network topology.
**Acceptance:** an MCP tool that takes a junctions geojson and a DEM
(GeoTIFF) and emits inverts; or, in the absence of a DEM, a documented
fallback policy (e.g. fixed offset below junction surface elevation)
declared in `missing_or_fallback_inputs`.

### F13 — Audit MCP entrypoint
**Status:** open (new from cold-start agent).
**The gap:** `swmm-end-to-end/SKILL.md` mandates running
`swmm-experiment-audit` after every run, but that skill has no MCP
server — it is a CLI tool. The cold-start agent skipped audit on time
budget; a more time-pressured agent would always skip it.
**Acceptance:** either (a) wrap the audit CLI in an MCP server so it
joins the same orchestration plane as the other 8 servers, or (b)
demote the SKILL.md "mandatory" language to "recommended" when no MCP
audit exists.

### F14 — Plot time-axis tick density
**Status:** open (new from cold-start agent).
**The gap:** `mcp/swmm-plot plot_rain_runoff_si` writes one X-axis
tick per timestep. For a 6-month daily simulation this is 182 ticks,
producing a black bar of overlapped labels (visible in
`saanich-cold-start-cecelia-20260513/outfall_flow.png`).
**Acceptance:** the plotter uses matplotlib `AutoDateLocator` /
`AutoDateFormatter`, **or** caps tick count to a configurable maximum
(default ~12). Optional `windowStart` / `windowEnd` args to crop the
plot range.

---

## Minor (polish, not blocking; small or low-impact)

### M1 — pyswmm OOM on import
**Status:** open. **From:** operator.

### M2 — Deterministic event-window selection
**Status:** open. **From:** operator.

### M3 — `swmm_peak` precision underflow
**Status:** open (re-confirmed by cold-start agent).
**The gap:** Both runs produced peak flows of order 10⁻⁴ m³/s, which
`swmm_peak` reads from the .rpt's 3-decimal summary block and rounds
to 0.000. The .out time-series shows the real peak — but
`swmm_peak` does not consult .out. This becomes blocking-for-validation
on any small catchment.
**Acceptance:** `swmm_peak` falls back to the .out binary when the
.rpt-derived peak is 0.0 but the .out has nonzero data for the
requested node.

---

## Suggested execution order

**Phase 1 (cold-start unblock for any region with full data) — done.**

1. **B4** (done): deps installable via `scripts/install_mcp_deps.sh`.
2. **B2** (done): mapping templates + decision README.
3. **B6** (done): `prepare_storm_inputs`.
4. **B5** (done): `reorient_pipes`.
5. **B3** (done): `infer_outfall`.
6. **B1** (done): `basin_shp_to_subcatchments`.
7. **F6** (done): `swmm_run` auto-detect outfall.
8. **F1** (done): extended landuse lookup + structured warnings.

**Phase 1 verification — done:** see
`saanich-e2e-new-tools-20260513/`. 12-step MCP chain ran end-to-end on
raw Saanich shapefiles with no hand-rolled python glue.

**Phase 2 (open, no longer blocking — proceed when prioritising
quality):**

- **F4** — diameter_policy as structured warning.
- **F10** — `outputPath` on `swmm_continuity` / `swmm_peak`.
- **F11** — soil-absence policy / synthesize uniform soil tool.
- **F12** — DEM-based invert inference (significant scope).
- **F2, F5, F9** — DX polish (defaults, options template, skill
  triggers).
- **F3, M2** — rainfall side (event-window selector / .dat unit
  documentation).
- **F7, F8, F13, F14** — testing / observability / UX polish (incl.
  plot x-axis density).
- **M1, M3** — tail-end.

Phase 2 items can be done in any order; none of them blocks the
"natural-language → autonomous pipe modeling for any region with full
data" path.

---

## Cross-references

- `saanich-smoke-20260513/cold_start_diagnostic.{md,json}` — 15-entry
  diagnostic from the operator's run.
- `saanich-cold-start-cecelia-20260513/README.md` — narrative of the
  cold-start agent's run, including 12 gaps it surfaced.
- `runs/` — ephemeral local working directories for both runs
  (`.gitignored`); regenerate by re-running Mode 0 (operator) or Mode A
  (cold-start agent) of `swmm-end-to-end`.
