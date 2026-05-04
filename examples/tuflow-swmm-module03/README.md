# TUFLOW SWMM Module 03 Raw GeoPackage Benchmark

This case study verifies the first half of the Agentic SWMM workflow on a public third-party dataset. Unlike a prepared-input benchmark, it starts from TUFLOW SWMM GeoPackage model layers and rebuilds the SWMM input through the repository's modular skills.

Source dataset:
- TUFLOW Wiki tutorial: https://wiki.tuflow.com/TUFLOW_SWMM_Tutorial_Introduction
- Tutorial package: https://downloads.tuflow.com/TUFLOW/Wiki_Tute_Models/TUFLOW_SWMM_Tutorial_Models_QGIS_GPKG.zip

## What This Benchmark Verifies

This case verifies the structured raw GIS-to-INP path:

```text
GeoPackage raw layers
-> junctions / outfalls / conduits / subcatchments / raingages
-> network.json
-> subcatchments.csv
-> params JSON
-> multi-raingage timeseries
-> generated model.inp
-> swmm5 run
-> continuity / peak QA
-> experiment audit
```

The source data are not committed to this repository because the tutorial archive is large. The benchmark script writes all local raw extracts and generated artifacts under `runs/`, which is ignored by Git.

## Model Scope

The benchmark uses the Module 03 complete model GeoPackage:

```text
TUFLOW_SWMM_Module_03/Complete_Model/TUFLOW/model/swmm/sw03_001.gpkg
```

The adapter reads these GeoPackage layers:

| Source layer | Generated artifact | Count |
| --- | --- | ---: |
| `Nodes--Junctions` | junction nodes in `network.json` | 14 |
| `Nodes--Outfalls` | outfall nodes in `network.json` | 1 |
| `Links--Conduits` | conduits in `network.json` | 14 |
| `Hydrology--Subcatchments` | `subcatchments.csv` and hydrology params | 14 |
| `Hydrology--Raingages` plus rainfall CSV | multi-raingage JSON and timeseries text | 2 |

The generated raingages are:

| Raingage | Timeseries |
| --- | --- |
| `RF_G1` | `RF_FC04` |
| `RF_G2` | `RF_FC07` |

Each rainfall timeseries has 23 rows, for 46 total `[TIMESERIES]` rows in the generated model.

## Reproduce

From the repository root, download the public tutorial package:

```bash
mkdir -p runs/raw-case-candidates/tuflow-swmm
curl -L \
  https://downloads.tuflow.com/TUFLOW/Wiki_Tute_Models/TUFLOW_SWMM_Tutorial_Models_QGIS_GPKG.zip \
  -o runs/raw-case-candidates/tuflow-swmm/TUFLOW_SWMM_Tutorial_Models_QGIS_GPKG.zip
unzip -q runs/raw-case-candidates/tuflow-swmm/TUFLOW_SWMM_Tutorial_Models_QGIS_GPKG.zip \
  -d runs/raw-case-candidates/tuflow-swmm
```

Then run:

```bash
python3 scripts/benchmarks/run_tuflow_swmm_module03_raw_path.py
```

The benchmark writes outputs under:

```text
runs/benchmarks/tuflow-swmm-module03-raw-path/
```

## Expected Artifacts

The benchmark produces:

```text
00_raw/junctions.geojson
00_raw/outfalls.geojson
00_raw/conduits.geojson
00_raw/subcatchments.geojson
00_raw/network_import_mapping.json
00_raw/params_from_gpkg.json
00_raw/raingage.json
00_raw/timeseries.txt
01_gis/subcatchments.csv
01_gis/subcatchments.json
04_network/network.json
04_network/network_qa.json
05_builder/model.inp
05_builder/manifest.json
06_runner/model.rpt
06_runner/model.out
06_runner/manifest.json
07_qa/continuity.json
07_qa/peak_Node20.json
manifest.json
experiment_provenance.json
comparison.json
experiment_note.md
```

## Expected QA

The latest local validation produced:

| Check | Expected |
| --- | --- |
| Network QA | `ok: true`, `issue_count: 0` |
| Generated subcatchments | 14 |
| Generated raingages | 2 |
| Generated junctions | 14 |
| Generated outfalls | 1 |
| Generated conduits | 14 |
| Timeseries rows | 46 |
| `swmm5` return code | 0 |
| `Node20` peak inflow | `3.62` CMS at `01:12` |
| Runoff continuity error | `-0.014%` |
| Flow routing continuity error | `0.002%` |

## Evidence Boundary

This benchmark is strong evidence that the repository can transform structured raw SWMM/GIS layers into an independently executable EPA SWMM model with traceable intermediate artifacts, QA outputs, and audit records.

It does not claim full greenfield watershed derivation from DEM, land use, soil, and raw drainage assets. That broader path still needs additional delineation and parameterization evidence. This case specifically proves:

```text
structured raw GeoPackage layers -> modular build artifacts -> runnable INP -> QA/audit
```

That boundary is important for paper wording: this is stronger than a prepared `.inp` benchmark, but it is not yet proof of automatic DEM-based watershed and pipe-network generation.
