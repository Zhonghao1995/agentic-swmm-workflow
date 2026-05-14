---
name: swmm-network
description: Build, validate, and route SWMM pipe-network models for urban drainage from raw municipal shapefiles or structured CAD/GIS exports. Use when handling junctions, conduits, outfalls, xsections, network field-mapping configs, or wiring subcatchments to upstream nodes.
---

# SWMM Network (pipe-system layer)

## What this skill provides

- A stable JSON schema for SWMM drainage-network structure.
- Two complementary import paths:
  - **Raw municipal shapefile path** (`prepare_storm_inputs` → `infer_outfall` → `reorient_pipes` → `import_city_network` → `qa`) for typical city storm-pipe + manhole layers that arrive as bare LineString shapefiles.
  - **Structured asset-DB path** (`import_city_network` directly, or `import_network` for a fully field-mapped GeoJSON/CSV) when the source already contains explicit from/to nodes, inverts, and diameters.
- A subcatchment-to-network wiring step (`assign_subcatchment_outlets`) that ensures surface runoff actually enters the pipe network at a real upstream junction rather than dumping straight to the outfall.
- Topology / hydraulic-attribute QA (`qa`).
- Lightweight introspection (`summary`).
- Export from network JSON to core SWMM INP sections (`export_inp`).

## When to use this skill

Use when a SWMM model needs a real pipe network. Specifically:

- You have municipal storm-pipe shapefile(s) and want them imported into a SWMM-ready network.json.
- You have a structured CAD/asset-DB export (CSV / GeoJSON with explicit topology) and want the same.
- You need to attach subcatchments to upstream junctions instead of letting them dump to the outfall.
- You need to QA an existing network.json before handing it to `swmm-builder`.

Do **not** use this skill when the user only wants subcatchment delineation (use `swmm-gis`) or only wants to run a finished INP (use `swmm-runner`).

## MCP tools

`mcp/swmm-network/server.js` exposes nine tools. Pick by what stage of the pipeline you're at.

### Raw-shapefile preparation chain

1. **`prepare_storm_inputs`** — clip raw `<municipal>StormGravityMain.shp` (+ optional `<municipal>StormManhole.shp`) to a basin polygon and emit pipes.geojson, manholes.geojson, and a filled mapping.json from a template.
   - Args: `pipesShpPath`, `manholesShpPath` (optional), `basinClipGeojsonPath`, `mappingTemplatePath`, `outDir`, `caseName`, `sourceDescription`, `diameterPolicy` (optional).
   - Use `templates/city_mapping_raw_shapefile.template.json` as the mapping template.
   - Does **not** pick the outfall, fix flow direction, or snap drifting endpoints (those are separate tools).

2. **`snap_pipe_endpoints`** — cluster nearby pipe endpoints (sub-millimetre to centimetre vertex drift) and snap each cluster to its centroid so adjacent pipes share identical endpoint coordinates. Without this, `import_city_network` infers separate junctions for drifting endpoints and the network ends up as disconnected fragments. Also drops pipes whose two endpoints collapse into the same cluster (self-loop conduits that SWMM rejects).
   - Args: `pipesGeojsonPath`, `toleranceM`, `outPath`.
   - Reports `pipes_in` / `pipes_out` / `pipes_dropped_as_self_loops` / `clusters_merged` / `max_snap_distance_m`.
   - Reasonable starting tolerance: 0.5–3 m for municipal storm pipe layers. Inspect the report before raising further.

3. **`infer_outfall`** — pick a single outfall point from pipe endpoints. Two modes:
   - `endpoint_nearest_watercourse` (default; needs a watercourse GeoJSON).
   - `lowest_endpoint` (uses min y, no watercourse needed; assumes a projected, north-positive CRS).
   - Args: `pipesGeojsonPath`, `watercourseGeojsonPath` (mode-dependent), `mode`, `outPath`.
   - Emits a single-Point outfalls.geojson (`node_id=OUT1`, `type=FREE`, `invert_elev=0.0`).

4. **`reorient_pipes`** — BFS from outfall vertices to flip LineString direction so it matches flow direction. Real municipal pipes are usually digitised arbitrarily and would otherwise produce bogus `from_node`/`to_node` assignments.
   - Args: `pipesGeojsonPath`, `outfallsGeojsonPath`, `outPath`, `coordinatePrecision` (default 3).
   - Reports `pipes_reversed`, `pipes_unreached` so connectivity gaps are visible.

### Network assembly

4. **`import_city_network`** — main adapter. Takes the prepared pipes+outfalls geojsons (or any structured pipe table) plus a mapping.json and emits `network.json` with inferred junctions if needed.
   - Args: `pipesCsvPath` OR `pipesGeojsonPath`, `outfallsCsvPath` OR `outfallsGeojsonPath`, optional junctions, `mappingPath`, `outputPath`.
   - For mapping.json: see `templates/README.md` (raw-shapefile vs structured-export shapes).

5. **`import_network`** — older field-mapped import for GeoJSON/CSV when topology and inverts are explicit per row. Prefer `import_city_network` for new work.

### Subcatchment wiring (REQUIRED for the pipe network to actually carry water)

6. **`assign_subcatchment_outlets`** — rewrite the `outlet` column of a subcatchments CSV so each subcatchment drains into a real upstream node (not the literal outfall). Without this step the pipe network sits idle in the SWMM model.
   - Args: `subcatchmentsCsvIn`, `subcatchmentsGeojson`, `outCsv`, `mode`.
   - Modes:
     - `nearest_junction` (default; needs `networkJsonPath`)
     - `nearest_catch_basin` (needs `candidatesGeojsonPath` + `candidatesIdField`)
     - `manual_lookup` (needs `lookupCsvPath` with columns `subcatchment_id,outlet_node_id`)

### QA + export

7. **`qa`** — run topology + required-attribute checks on a `network.json`. Args: `networkJsonPath`. Returns a structured QA report (warnings include `no_outfall_path`, missing inverts, etc.).

8. **`export_inp`** — render a `network.json` to SWMM INP sections (junctions/outfalls/conduits/xsections/coordinates). Args: `networkJsonPath`. Used internally by `swmm-builder`; rarely called directly by an agent.

9. **`summary`** — quick counts (junctions, outfalls, conduits, total length, system_layers, dual-system-ready flag). Args: `networkJsonPath`. For diagnostics.

## Recommended orchestration

For a raw municipal shapefile dataset, the canonical chain is:

```
prepare_storm_inputs       → pipes.geojson + manholes.geojson + mapping.json
snap_pipe_endpoints        → pipes_snapped.geojson  (heal vertex drift; also drops self-loop pipes)
infer_outfall              → outfalls.geojson
reorient_pipes             → pipes_oriented.geojson
import_city_network        → network.json
qa                         → ok / warnings
assign_subcatchment_outlets → subcatchments_routed.csv  (required if subcatchments came from swmm-gis basin_shp_to_subcatchments)
   ↓
hand off to swmm-builder.build_inp
```

For a structured CAD export with explicit from/to nodes, skip `prepare_storm_inputs`/`infer_outfall`/`reorient_pipes` and call `import_city_network` directly with the CSVs.

## Templates and examples

- `templates/city_mapping_raw_shapefile.template.json` — fully-specified mapping for raw LineString-only pipe shapefiles. The adapter infers junctions from endpoints. Used by the `prepare_storm_inputs` chain.
- `templates/README.md` — decision walkthrough between the two mapping shapes.
- `examples/city-dual-system/mapping.json` — fully-specified mapping for structured exports with explicit from/to/x/y/invert columns.
- `examples/import-mapping.json` + `examples/import-junctions.geojson` etc. — example inputs for the older `import_network` path.

## Scripts (Python implementations behind the MCP tools)

- `scripts/prepare_storm_inputs.py` — backs `prepare_storm_inputs`.
- `scripts/infer_outfall.py` — backs `infer_outfall`.
- `scripts/reorient_pipes.py` — backs `reorient_pipes`.
- `scripts/city_network_adapter.py` — backs `import_city_network`.
- `scripts/network_import.py` — backs `import_network`.
- `scripts/assign_subcatchment_outlets.py` — backs `assign_subcatchment_outlets`.
- `scripts/network_qa.py` — backs `qa`.
- `scripts/network_to_inp.py` — backs `export_inp`.
- `scripts/minimal_stub_network.py` — emits a 1-junction + 1-outfall stub `network.json` from a subcatchment shapefile that carries OUTLET/X/Y attrs. Use only for real-data smoke tests when no pipe-network geometry exists yet; the resulting network must not be treated as a calibrated drainage system.
- `scripts/schema/network_model.schema.json` — stable schema target.

## Conventions

- Prefer explicit, machine-readable JSON in/out.
- Keep node/link IDs unique and stable; the adapter generates `J_AUTO_<x>p<y>` IDs for inferred junctions.
- MVP assumes gravity-network basics first (no pumps/weirs/orifices).
- Dual-system-ready currently means representation and QA metadata, not fully coupled 1D/2D hydraulics.
- All polygon area / distance calculations assume a projected CRS — the tools error early if a geographic CRS is supplied.

## Known limitations

- Pipe inverts default to 0.0 m when not provided. A DEM-based invert inference tool is open as `BACKLOG.md F12`.
- `infer_outfall` always emits exactly one outfall (`OUT1`). Multi-outfall networks need a follow-up tool.
- `snap_pipe_endpoints` only heals vertex drift, not physically missing pipes. If a basin clip cuts out a trunk sewer that connects two sub-graphs, the sub-graphs remain disconnected. A future "buffered basin clip" feature in `prepare_storm_inputs` would address this.
