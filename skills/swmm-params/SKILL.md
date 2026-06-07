---
name: swmm-params
description: Deterministic mapping from land use and soil texture to SWMM runoff/subarea and Green-Ampt infiltration parameters. Use when generating first-pass subcatchment parameter tables for swmm-builder.
---

# SWMM Params (MVP mapping layer)

## What this skill provides
- Transparent CSV-to-JSON mapping for:
  - land use class -> SWMM `[SUBCATCHMENTS]` + `[SUBAREAS]` defaults
  - soil texture/type -> SWMM `[INFILTRATION]` (Green-Ampt) defaults
- Deterministic, auditable outputs with explicit fallback usage and unmatched-key reporting.
- Optional merge step that emits one builder-ready JSON artifact.

## Scripts
- `scripts/landuse_to_swmm_params.py`
  - maps `subcatchment_id + landuse_class` to runoff/subarea parameters
- `scripts/soil_to_greenampt.py`
  - maps `subcatchment_id + soil_texture` to Green-Ampt infiltration parameters
- `scripts/merge_swmm_params.py`
  - merges outputs from the two mapping scripts into one JSON package for future `swmm-builder`

## Default lookup tables
By default, scripts read bundled lookup CSVs:
- `skills/swmm-params/references/landuse_class_to_subcatch_params.csv`
- `skills/swmm-params/references/soil_texture_to_greenampt.csv`

You can override lookup paths with CLI flags.

## Minimal input format
Land use input CSV:
- required columns: `subcatchment_id`, `landuse_class`

Soil input CSV:
- required columns: `subcatchment_id`, `soil_texture`

Example files are provided under `examples/`.

## Outputs
Each mapper writes explicit JSON containing:
- `records` (row-level audit trail)
- `sections` (SWMM-oriented lists keyed by subcatchment)
- `unmatched_*` lists (rows that used fallback)
- `counts` summary

The merge script writes:
- `sections` (`subcatchments`, `subareas`, `infiltration`)
- `by_subcatchment` (combined record per subcatchment ID)
- `incomplete_ids` (IDs missing one or more sections)

## MCP
MCP wrapper location:
- `mcp/swmm-params/server.js`

Exposed tools:
- `map_landuse` (`inputCsvPath`, optional `lookupCsvPath`, `outputPath`)
- `map_soil` (`inputCsvPath`, optional `lookupCsvPath`, `outputPath`)
- `merge_params` (`landuseJsonPath`, `soilJsonPath`, `outputPath`)

Quick start:
```bash
npm --prefix mcp/swmm-params install
npm --prefix mcp/swmm-params run start
```

## MVP limitations
- Lookup mapping is key-based only (no spatial interpolation or fuzzy matching).
- A fallback row is expected in lookup tables (`DEFAULT` for land use, `-` or `DEFAULT` for soil).
- No unit conversion or calibration logic is included here.
- This skill only maps parameters; it does not write a full SWMM `.inp`.

## Example commands
```bash
python3 skills/swmm-params/scripts/landuse_to_swmm_params.py \
  --input skills/swmm-params/examples/landuse_input.csv \
  --output runs/swmm-params/example_landuse.json
```

```bash
python3 skills/swmm-params/scripts/soil_to_greenampt.py \
  --input skills/swmm-params/examples/soil_input.csv \
  --output runs/swmm-params/example_soil.json
```

```bash
python3 skills/swmm-params/scripts/merge_swmm_params.py \
  --landuse-json runs/swmm-params/example_landuse.json \
  --soil-json runs/swmm-params/example_soil.json \
  --output runs/swmm-params/example_builder_params.json
```
