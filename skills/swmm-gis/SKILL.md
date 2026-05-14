---
name: swmm-gis
description: GIS/DEM preprocessing for SWMM experiments. Use when Zhonghao asks to (1) delineate subcatchments through QGIS/GRASS (standard or entropy-guided), (2) preprocess QGIS-derived subcatchment polygons into builder-ready CSV, (3) identify high-entropy hotspot subcatchments, or (4) expose QGIS/GRASS-backed preprocessing as MCP tools for reproducible workflows.
---

# SWMM GIS / Preprocess

## Before calling any watershed delineation tool — ask the user

When Zhonghao triggers watershed delineation (`qgis_raw_to_entropy_partition` or equivalent), **always ask these questions first** before making the tool call:

1. **Delineation mode** — Standard (fast, direct GRASS basins, no entropy) or Entropy-guided (paper WJE/NWJE/WFJS split-lump with sensitivity figures)?
2. **Stream threshold** — How many upslope cells define a stream? Default 100. Smaller = more streams = finer subcatchments.
3. **If entropy mode** — Delta threshold (default 0.015) and WFJS similarity threshold (default 0.95)? Use defaults unless doing sensitivity exploration.
4. **Purpose** — Planning / calibration exploration / sensitivity analysis / paper reproduction? This affects how strictly to apply paper-only splits and whether sensitivity figures are needed.
5. **CRS normalization needed?** — Are all input layers already in the same projected CRS? If uncertain, check first with `qgis_load_layers` + `qgis_validate_crs`.

Do not assume entropy mode. Do not skip the stream threshold question — it directly controls subcatchment count.

Default CRS policy: if source layers already share the same projected CRS, do **not** run `normalize-layers`. The normalization bridge reprojects, clips, and may resample raster grids, so it can change watershed structure. Only use it when layer CRS/raster alignment actually needs preprocessing. If CRS differs but geometry should be preserved, prefer a reproject-only step over clipping/resampling.

## Choosing the right delineation mode

| | Standard | Entropy-guided |
|---|---|---|
| **Speed** | Fast (~minutes) | Slow (~10–30 min, 5 sensitivity variants) |
| **Output** | GRASS basin polygons only | WJE/NWJE/WFJS partition + sensitivity figures + entropy hotspot ranking |
| **Use when** | Quick first look, simple watersheds, testing pipeline connectivity | Research, paper reproduction, heterogeneous land-use/soil, need to justify subcatchment count |
| **MCP flag** | `mode: "standard"` | `mode: "entropy"` (default) |

## Entropy hotspot ranking

After an entropy run, `audit/entropy_hotspot_ranking.json` ranks subcatchments by WJE descending. Rank 1 = highest spatial heterogeneity = candidate for finer delineation in calibration. Surface this to the user if they ask "which subcatchments matter most" or "where should I refine."

## What this skill provides
- Subcatchment polygon preprocessing (MVP):
  - ingest polygon GeoJSON
  - estimate area/width/slope with deterministic fallback and optional DEM-assisted metrics
  - link each subcatchment outlet to a network node ID
  - export builder-ready CSV for `swmm-builder`
- QGIS-oriented raw-data entrypoint:
  - validate raw/QGIS-exported layer paths and shapefile sidecars
  - inspect CRS hints from `.prj` and GeoJSON metadata
  - run QGIS Processing / GRASS hydrology for flow accumulation, drainage direction, stream network, and basin labels
  - compute paper-consistent WJE/NWJE/WFJS entropy diagnostics along the longest D8 flow path
  - generate entropy-guided subcatchment partitions and threshold sensitivity figures
  - extract QGIS overlay attributes into `swmm-params` CSV inputs
  - export standard Agentic SWMM intermediates under `runs/<case>/01_gis/`, `02_params/`, and `04_network/`
- Clean final layer packaging:
  - keep detailed audit artifacts in `00_raw/`, `01_gis/`, `02_params/`, `audit/`, and `memory/`
  - also create a user-facing `final_layers/` folder with the SWMM/GIS layers Zhonghao needs next
  - include `subcatchments.shp`, `flow.shp`, `slope_percent.tif`, `outfall.shp`, `overview.png`, and `manifest.json`

## Scripts
- `scripts/preprocess_subcatchments.py`
  - `--subcatchments-geojson <file>`
  - `--network-json <file>` (from `swmm-network` schema)
  - `--out-csv <file>` (builder-ready CSV)
  - `--out-json <file>` (assumptions + detailed metrics)
  - optional DEM mode: `--dem-stats-json <file>`, `--dem-stats-id-field <field>`
  - optional helpers: `--id-field`, `--outlet-hint-field`, `--default-slope-pct`, `--min-width-m`, `--max-link-distance-m`

- `scripts/qgis_prepare_swmm_inputs.py`
  - `load-layers`: validate source paths and shapefile sidecars
  - `validate-crs`: write a CRS consistency report from a layer manifest
  - `normalize-layers`: use QGIS Processing to reproject DEM, boundary, land-use, and soil layers to one CRS and clip them by the boundary
  - `overlay-landuse-soil`: convert a QGIS overlay GeoJSON into `landuse.csv` and `soil.csv`
  - `export-swmm-intermediates`: produce the standard data-side outputs for the modular path:
    - `runs/<case>/00_raw/qgis_layers_manifest.json`
    - `runs/<case>/00_raw/qgis_crs_report.json`
    - `runs/<case>/01_gis/subcatchments.{geojson,csv,json}`
    - `runs/<case>/02_params/{landuse.csv,soil.csv,landuse.json,soil.json,merged_params.json}`
    - `runs/<case>/04_network/{network.json,network_qa.json}`
    - `runs/<case>/qgis_export_manifest.json`

- `scripts/flowpath_entropy_partition.py`
  - computes paper-consistent spatial heterogeneity diagnostics:
    - `WJE(g)` over upstream contributing area `U(g)`
    - `NWJE(g) = WJE(g) / ln(D(g))`
    - upstream-averaged fuzzy memberships for soil drainage, land-use perviousness, and slope
    - `WFJS_seq` between adjacent cells on the longest flow path
    - `WFJS_outlet` relative to the outlet profile
    - `delta_NWJE_seq` and `delta_NWJE_outlet`
  - default paper split rule:
    - preserve/split where `abs(delta_NWJE_seq) >= 0.015` and `WFJS_seq <= 0.95`
    - safe lump / HP-REA interval where `abs(delta_NWJE_seq) <= 0.015` and `WFJS_seq >= 0.95`
  - `--paper-only-splits` disables secondary engineering split points for publication-style or paper-rule-only outputs

- `scripts/cell_entropy_similarity_aggregation.py`
  - non-flow-connected local aggregation diagnostic:
    - computes normalized joint entropy in a moving cell window from soil / land-use / slope triples
    - computes adjacent-cell fuzzy Jaccard similarity from soil / land-use / slope membership vectors
    - labels cells as `lumpable`, `transitional`, or `preserve_discrete`
  - use this before hydrologic routing as a data-side heterogeneity screen; do not treat it as upstream WJE/NWJE or a watershed delineation result

- `scripts/plot_entropy_threshold_sensitivity.py`
  - renders five-panel paper-rule decision-space and watershed-partition figures
  - uses Arial 12 pt styling and non-overlapping figure-level legends

- `scripts/qgis_raw_to_entropy_partition.py`
  - reproducible one-command cross-watershed raw GIS runner:
    - validates source GIS layers
    - optionally normalizes CRS and clips DEM / boundary / land-use / soil layers by boundary
    - calls QGIS Processing `grass:r.watershed`
    - runs paper-rule entropy partitioning
    - runs threshold sensitivity variants
    - writes audit manifests, command logs, figures, and run memory cards

- `scripts/qgis_todcreek_raw_to_entropy_partition.py`
  - compatibility wrapper around `qgis_raw_to_entropy_partition.py` for the committed Tod Creek case study

- `scripts/qgis_package_final_layers.py`
  - packages QGIS/GRASS run outputs into `runs/<case>/final_layers/`
  - copies/renames the selected subcatchment shapefile to `subcatchments.shp`
  - copies the DEM-derived slope raster to `slope_percent.tif`
  - derives `flow.shp` from QGIS/GRASS `stream_<threshold>.tif` plus `acc_<threshold>.tif`
  - derives `outfall.shp` from the maximum flow-accumulation stream cell
  - writes `overview.png` using Arial, inward ticks, longitude/latitude border labels, green-low/red-high semi-transparent slope background, bold subcatchment boundaries, prominent blue flow paths, and a legend
  - writes `manifest.json` so users do not need to inspect the audit tree to find deliverables

- `scripts/plot_qgis_standard_layers.py`
  - renders the clean `final_layers/overview.png`
  - intended for deliverable figures, not raw audit screenshots

## Explicit assumptions for subcatchment preprocessing
- Coordinates should be in one projected CRS before SWMM geometric quantities are trusted. Use `qgis_normalize_layers` or `--normalize-layers` when raw DEM / land-use / soil / boundary inputs may be mixed CRS or not clipped to the study boundary.
- Width helper priority:
  1. `properties.width_m` / `properties.hydraulic_width_m`
  2. DEM flow length (`dem_flow_length_m`) via `area_m2 / flow_length_m`
  3. fallback `width_m = max(min_width_m, 2 * area_m2 / perimeter_m)`
- Slope helper priority:
  1. `properties.slope_pct`
  2. DEM direct slope (e.g., `dem_slope_pct`, `raster_slope_pct`)
  3. DEM elevation-derived slope (e.g., `dem_elev_max_m`, `dem_elev_min_m`, `dem_elev_mean_m`, `dem_elev_outlet_m`)
  4. `(properties.elev_mean_m - properties.elev_outlet_m) / flow_length_m * 100`
  5. default slope
- Outlet linking priority:
  1. valid `properties.outlet_hint` (or configured field)
  2. nearest node ID from network coordinates (fallback with diagnostics)

## DEM-assisted example
```bash
python3 skills/swmm-gis/scripts/preprocess_subcatchments.py \
  --subcatchments-geojson skills/swmm-gis/examples/subcatchments_dem_assisted.geojson \
  --network-json skills/swmm-network/examples/basic-network.json \
  --dem-stats-json skills/swmm-gis/examples/subcatchments_dem_stats_demo.json \
  --default-rain-gage RG1 \
  --out-csv runs/swmm-gis/subcatchments_dem_assisted.csv \
  --out-json runs/swmm-gis/subcatchments_dem_assisted.json
```

## QGIS data-prep example
Use this when QGIS has already delineated subcatchments and overlaid land-use / soil attributes onto the subcatchment layer:

```bash
python3 skills/swmm-gis/scripts/qgis_prepare_swmm_inputs.py export-swmm-intermediates \
  --case-id qgis-demo \
  --run-dir runs/qgis-demo \
  --subcatchments-geojson skills/swmm-gis/examples/qgis_overlay_subcatchments.geojson \
  --network-json skills/swmm-network/examples/basic-network.json \
  --landuse-field landuse_class \
  --soil-field soil_texture \
  --default-rain-gage RG1
```

This bridge supports two modes. Prepared-overlay mode expects QGIS to provide delineated/overlayed polygons. Entropy-partition mode calls QGIS Processing / GRASS hydrology directly, then computes the paper-rule WJE/NWJE/WFJS split-lump partition inside Agentic SWMM.

## QGIS/GRASS entropy-guided subcatchment example
Use this for the full raw GIS to paper-rule subcatchment workflow. QGIS/GRASS provides the hydrology backbone; Agentic SWMM computes the paper-consistent entropy/fuzzy split-lump logic and writes audit artifacts.

Generic form for any watershed with DEM, boundary, land-use, and soil layers:

```bash
python3 skills/swmm-gis/scripts/qgis_raw_to_entropy_partition.py \
  --case-id my-watershed-qgis-entropy \
  --case-label "My Watershed" \
  --dem path/to/dem.tif \
  --boundary path/to/boundary.shp \
  --landuse path/to/landuse.shp \
  --soil path/to/soil.shp \
  --out-dir runs/my-watershed-qgis-entropy
```

Use normalization when raw layers need CRS harmonization and boundary clipping before hydrology:

```bash
python3 skills/swmm-gis/scripts/qgis_raw_to_entropy_partition.py \
  --case-id my-watershed-qgis-entropy \
  --case-label "My Watershed" \
  --dem path/to/dem.tif \
  --boundary path/to/boundary.shp \
  --landuse path/to/landuse.shp \
  --soil path/to/soil.shp \
  --normalize-layers \
  --out-dir runs/my-watershed-qgis-entropy
```

Tod Creek case-study command:

```bash
python3 skills/swmm-gis/scripts/qgis_raw_to_entropy_partition.py \
  --case-id todcreek-qgis-entropy \
  --case-label "Tod Creek" \
  --dem data/Todcreek/Geolayer/n48_w124_1arc_v3_Clip_Projec1.tif \
  --boundary data/Todcreek/Boundary/Boundary.shp \
  --landuse data/Todcreek/Geolayer/landuse.shp \
  --soil data/Todcreek/Geolayer/soil.shp \
  --rainfall data/Todcreek/Rainfall/1984rain.dat \
  --out-dir runs/todcreek-qgis-entropy
```

The run writes:

```text
runs/<case>/00_raw/qgis_layers_manifest.json
runs/<case>/00_raw/qgis_crs_report.json
runs/<case>/00_raw/normalized_layers/qgis_normalized_layers_manifest.json  # if --normalize-layers
runs/<case>/01_gis/threshold_sweep/{acc,drain,basin,stream}_100.tif
runs/<case>/02_params/paper_entropy_partition/
runs/<case>/02_params/threshold_sensitivity/
runs/<case>/07_figures/paper_rule_decision_spaces_5panel.png
runs/<case>/07_figures/paper_rule_watershed_partitions_5panel.png
runs/<case>/audit/qgis_entropy_run_manifest.json
runs/<case>/audit/processing_commands.json
runs/<case>/memory/qgis_entropy_subcatchment_memory.{json,md}
runs/<case>/final_layers/{subcatchments.shp,flow.shp,slope_percent.tif,outfall.shp,overview.png,manifest.json}  # after packaging
```

Evidence boundary: this workflow produces GIS-derived SWMM subcatchment spatial units and audit evidence. It does not prove calibrated hydrologic performance until the outputs are passed through `swmm-builder`, `swmm-runner`, and `swmm-experiment-audit`.

## Cell-level entropy/similarity aggregation diagnostic
Use this when the question is local data aggregation before hydrologic routing: where can adjacent raster cells be lumped because they are information-similar, and where should local spatial heterogeneity be preserved?

```bash
python3 skills/swmm-gis/scripts/cell_entropy_similarity_aggregation.py \
  --dem data/Todcreek/Geolayer/n48_w124_1arc_v3_Clip_Projec1.tif \
  --boundary-shp data/Todcreek/Boundary/Boundary.shp \
  --landuse-shp data/Todcreek/Geolayer/landuse.shp \
  --soil-shp data/Todcreek/Geolayer/soil.shp \
  --out-dir runs/todcreek-cell-entropy-aggregation
```

This diagnostic does not use flow accumulation, drainage direction, or upstream contributing area `U(g)`. It is useful for a pre-flow data heterogeneity layer, while `qgis_flowpath_entropy_partition` remains the hydrologically connected SWMM subcatchment partition.

## MCP-facing operations

`mcp/swmm-gis/server.js` exposes 15 tools. They split into three families.

### Subcatchment construction (start here for raw municipal data)
- `basin_shp_to_subcatchments`: pick polygons from any municipal basin / catchment shapefile and emit SWMM-ready `subcatchments.geojson` + `subcatchments.csv` (subcatchment_id, outlet, area_ha, width_m, slope_pct, rain_gage). Four selection modes: `by_id_field` (default), `by_index`, `largest`, `all`. Width defaults to `sqrt(area_m²)`; slope defaults to 1%. Use this as step 1 when starting from raw shapefile data.
- `gis_preprocess_subcatchments`: deterministic preprocessor used by both the explicit DEM-assisted path and the legacy non-MCP scripts. Computes width/slope/area from a basin shapefile + DEM. Use when a DEM is available.

### Standard QGIS data-prep chain
- `qgis_load_layers`: validate source files and sidecars.
- `qgis_validate_crs`: check that explicit CRS hints are consistent before export.
- `qgis_normalize_layers`: reproject DEM, boundary, land-use, and soil layers to a target CRS, then clip them by the boundary. Uses QGIS Processing `native:reprojectlayer`, `native:clip`, `gdal:warpreproject`, and `gdal:cliprasterbymasklayer`.
- `qgis_overlay_landuse_soil`: extract overlay attributes into the `swmm-params` input CSV format.
- `qgis_extract_slope_area_width`: call the deterministic subcatchment preprocessor.
- `qgis_import_drainage_assets`: copy/import a network JSON and run network QA.
- `qgis_export_swmm_intermediates`: run the complete MVP data-side bridge.

### Entropy-guided partition (research-grade, optional)
- `qgis_raw_to_entropy_partition`: run the full cross-watershed raw GIS → QGIS/GRASS hydrology → paper-rule entropy subcatchment workflow with audit artifacts. Region-agnostic.
- `qgis_todcreek_raw_to_entropy_partition`: case-study alias for the committed Tod Creek regression. Don't use for new regions; pass your own paths to `qgis_raw_to_entropy_partition` instead.
- `qgis_flowpath_entropy_partition`: run the core paper-rule WJE/NWJE/WFJS partition from already prepared QGIS/GRASS flow accumulation and drainage rasters.
- `qgis_package_final_layers`: package the selected QGIS/GRASS outputs into a clean `final_layers/` deliverable folder with SWMM/GIS layers, overview figure, and manifest.
- `qgis_cell_entropy_similarity_aggregation`: non-flow-connected local cell-level entropy/similarity aggregation diagnostic.

### Area-weighted parameter mapping (core for any region)
- `qgis_area_weighted_params`: intersect subcatchments with land-use and soil polygons, compute area fractions, and write area-weighted `weighted_params.json` plus `landuse_area_weights.csv` and `soil_area_weights.csv` audit files. This is the canonical handoff into `swmm-builder`. Backed by `skills/swmm-params/references/landuse_class_to_subcatch_params.csv` (extend that lookup if your region's zoning vocabulary is unfamiliar).

Future QGIS processing should fill the same interfaces rather than changing downstream `swmm-params`, `swmm-network`, or `swmm-builder` contracts.

## Notes
- These steps occur **before** generating SWMM INP.
- CSV/JSON outputs include `*_source` / `*_method` fields for auditability.
