---
name: swmm-gis
description: GIS/DEM preprocessing for SWMM experiments. Use when Zhonghao asks to (1) find a pour point/outlet from a DEM, (2) preprocess subcatchment polygons into builder-ready CSV, (3) link subcatchments to network node IDs with deterministic rules, or (4) expose preprocessing as MCP tools for reproducible workflows.
---

# SWMM GIS / Preprocess

## What this skill provides
- Pour point (outlet) selection from a DEM:
  - `boundary_min_elev`: minimum elevation cell on DEM boundary
  - `boundary_max_accum`: maximum D8 flow-accumulation cell on DEM boundary (with depression filling + flat resolution)
- Subcatchment polygon preprocessing (MVP):
  - ingest polygon GeoJSON
  - estimate area/width/slope with deterministic fallback and optional DEM-assisted metrics
  - link each subcatchment outlet to a network node ID
  - export builder-ready CSV for `swmm-builder`

## Scripts
- `scripts/find_pour_point.py`
  - `--dem <tif>`
  - `--method boundary_min_elev|boundary_max_accum`
  - `--out-geojson <file>`
  - `--out-png <file>`

- `scripts/preprocess_subcatchments.py`
  - `--subcatchments-geojson <file>`
  - `--network-json <file>` (from `swmm-network` schema)
  - `--out-csv <file>` (builder-ready CSV)
  - `--out-json <file>` (assumptions + detailed metrics)
  - optional DEM mode: `--dem-stats-json <file>`, `--dem-stats-id-field <field>`
  - optional helpers: `--id-field`, `--outlet-hint-field`, `--default-slope-pct`, `--min-width-m`, `--max-link-distance-m`

## Explicit assumptions for subcatchment preprocessing
- Coordinates are treated as planar meters (no reprojection in MVP).
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

## Notes
- These steps occur **before** generating SWMM INP.
- CSV/JSON outputs include `*_source` / `*_method` fields for auditability.
