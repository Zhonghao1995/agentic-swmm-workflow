---
name: swmm-network
description: Build and validate SWMM pipe-network models for urban drainage systems. Use when defining or checking junctions, conduits, outfalls, xsections, coordinates, or exporting network JSON into SWMM INP sections.
---

# SWMM Network (pipe-system layer)

## What this skill provides
- A stable JSON schema for SWMM drainage-network structure
- MVP import from GeoJSON/CSV into the network schema via field-mapping config
- Basic QA for topology and required hydraulic attributes
- Export from network JSON to core SWMM INP sections
- MCP skeleton for `qa`, `export_inp`, and `summary`

## MVP scope
This skill currently focuses on the **pipe-system/network** layer only:
- junctions
- outfalls
- conduits
- xsections
- coordinates
- optional vertices

It does **not** yet generate networks from GIS/DEM automatically. It expects a network JSON that follows the schema.

## Scripts
- `scripts/network_import.py`
  - imports GeoJSON/CSV network data into the stable network JSON schema using a field-mapping config
- `scripts/network_qa.py`
  - validates network JSON and writes a machine-readable QA report
- `scripts/network_to_inp.py`
  - exports network JSON to SWMM INP sections
- `scripts/schema/network_model.schema.json`
  - stable schema target for future GIS import/synthesis tools

## Conventions
- Prefer explicit, machine-readable JSON in/out.
- Keep node/link IDs unique and stable.
- MVP assumes gravity network basics first, not pumps/weirs/orifices.
- Future skills/tools can target this schema and then hand off to `swmm-runner` and `swmm-calibration`.
