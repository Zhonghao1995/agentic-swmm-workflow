---
name: swmm-gis
description: GIS/DEM preprocessing for SWMM experiments. Use when Zhonghao asks to (1) find a pour point/outlet from a DEM (boundary min elevation or boundary max flow accumulation), (2) export outlet as GeoJSON for QGIS, (3) create quick DEM+outlet preview figures, or (4) expose these preprocessing steps as MCP tools for a reproducible stormwater workflow.
---

# SWMM GIS / Preprocess

## What this skill provides
- Pour point (outlet) selection from a DEM:
  - **boundary_min_elev**: minimum elevation cell on DEM boundary
  - **boundary_max_accum**: maximum D8 flow-accumulation cell on DEM boundary (with depression filling + flat resolution)
- Outputs:
  - `*.geojson` point (same CRS as DEM)
  - preview `*.png` (DEM + outlet marker)

## Scripts
- `scripts/find_pour_point.py`
  - `--dem <tif>`
  - `--method boundary_min_elev|boundary_max_accum`
  - `--out-geojson <file>`
  - `--out-png <file>`

## Notes
- These steps occur **before** generating SWMM INP (outfall location is part of the model definition).
- Always record method + output paths in the run manifest for provenance.
