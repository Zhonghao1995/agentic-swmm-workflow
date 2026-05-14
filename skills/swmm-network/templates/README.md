# `mapping.json` templates for `import_city_network`

The `swmm-network-mcp.import_city_network` tool (and the underlying
`city_network_adapter.py` script) require a `mapping.json` config that
describes how the raw input columns map to the SWMM network model.

There are two practical shapes of `mapping.json` because there are two
practical shapes of input data. **Pick the one that matches what you
actually have, then edit it.**

---

## Style 1 — structured / dual-system export (CAD or asset-DB origin)

Use when your pipes table has **explicit `from_node` / `to_node` columns**
and per-pipe `from_x / from_y / to_x / to_y` and invert elevations. This is
typical of structured CAD or asset-DB exports.

**Template:** `../examples/city-dual-system/mapping.json`
**Example input:** `../examples/city-dual-system/pipes.csv` and friends.

Salient features:

- `pipes.fields` enumerates `from_node`, `to_node`, `from_x`, `from_y`,
  `to_x`, `to_y`, `from_invert_elev`, `to_invert_elev`, `diameter`,
  `roughness`, `material` — the adapter trusts these directly.
- `dual_system_ready: true` is allowed and meaningful.
- Junction inference is rarely used (junctions usually come in as their
  own explicit CSV).

---

## Style 2 — raw municipal storm shapefile (LineString pipes only)

Use when your pipes geojson is a **bare LineString layer** (typical of a
municipal `StormGravityMain.shp` export). Pipes have no `from_node` /
`to_node` columns; coordinates only exist as geometry vertices; invert
elevations are usually absent; some attribute fields (e.g. `DIAMETER`)
may contain nonnumeric values.

**Template:** `city_mapping_raw_shapefile.template.json`
**Reference real-world use:** `docs/framework-validation/saanich-smoke-20260513/`
contains a working mapping derived from this template for Saanich data.

Salient features:

- `pipes.fields` is **sparse on purpose** — only the few fields the
  shapefile actually provides (`FACILITYID`, `CRSECSHAPE`, `MATERIAL`).
  The adapter then **infers junctions from pipe endpoints** rather than
  looking them up.
- `pipes.defaults.geom1` is the fallback diameter applied when the raw
  DIAMETER field is missing or nonnumeric. Document the policy in
  `meta.diameter_policy` so the manifest captures it.
- `pipes.defaults.from_invert_elev / to_invert_elev` default to 0.0 m.
  SWMM will warn about elevation drops; this is intentional for smoke
  runs and should be replaced by a DEM-based invert inference once
  available (see `BACKLOG.md F12`).
- `dual_system_ready: false`.
- `inference.junction_prefix: "J_AUTO"` causes inferred junctions to be
  named `J_AUTO_<x>p<y>_<...>`.

### What this template does NOT do

Two known gaps live outside the mapping config and need separate
remediation:

1. **Outfall identification.** The mapping config does not pick the
   outfall node; you must supply a separate `outfalls.geojson` to
   `import_city_network`. See `BACKLOG.md B3`.
2. **Pipe orientation.** The adapter treats pipe geometry direction as
   flow direction. For raw municipal shapefiles this is rarely true.
   The first `qa` call will flag `no_outfall_path` warnings. See
   `BACKLOG.md B5`.

When (B3) and (B5) are closed, those steps will be invoked by a separate
MCP tool and remain orthogonal to this mapping config.

---

## How to use a template

1. Copy the template into your run directory (e.g.
   `runs/<case>/04_network/mapping.json`).
2. Replace placeholder strings under `meta.*` with your case-specific
   strings.
3. If your shapefile uses different field names than the defaults
   (e.g. `OBJECTID` rather than `FACILITYID`), edit `pipes.fields.*`.
4. Adjust `defaults.geom1` to a sensible diameter for unknown pipes in
   your dataset.
5. Pass the file via `--arguments-json '{"mappingPath": "<path>"}'` when
   invoking the MCP tool.

The adapter does not validate the mapping config against a schema yet;
typos in `fields.*` will surface as "field not found" errors at import
time.
