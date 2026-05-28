# swmm-anywhere

**Synthesize a plausible SWMM drainage network from public data (OSM streets + DEM) when no real pipe-network data exists.**

Use **ONLY** when the user explicitly does not have pipe shapefile / CAD / GIS data, or when establishing a baseline before real data arrives. **Do NOT use if `swmm-gis` or `swmm-network` can run on the user's actual data** — the synthesized network is an *inferred plausibility*, not measured infrastructure.

For bbox-only inputs without real pipe data: this is the right skill.
For inputs that include a `.shp`, `.csv`, or `network.json` of real pipes: route to `swmm-network` or `swmm-gis` instead.

## What this skill does

Given a bounding box (and optional region name), this skill:

1. **Downloads public source data** via SWMManywhere: OpenStreetMap streets, a DEM tile (Planetary Computer by default), building footprints, river lines.
2. **Snapshots the raw inputs** under `runs/<date>/<id>/00_raw/` with a SHA-256 manifest so the synthesized network is reproducible by snapshot (OSM/DEM otherwise drift continuously upstream).
3. **Runs SWMManywhere's 24-step graph pipeline** to infer subcatchment polygons, manhole nodes, pipe topology, pipe diameters, and outfall locations.
4. **Writes a SWMM 5.2 `.inp`** under `runs/<date>/<id>/10_swmmanywhere/synth.inp`, post-processed so the aiswmm `swmm5` binary can run it directly (external `storm.dat` is copied next to the INP and its path is rewritten as relative, dodging the macOS path-with-spaces parsing bug).
5. **Returns** the INP path, raw-snapshot manifest path, and a structured provenance record (which graphfcns ran, parameter overrides used, upstream tool versions).

The synthesized INP is **immediately runnable** through `swmm-runner` and **immediately auditable** through `swmm-experiment-audit`.

## Required inputs

- `--bbox`: four floats `min_lon min_lat max_lon max_lat` (WGS84). 1×1 km is a comfortable test size; smaller is faster and uses less RAM, larger needs more.
- `--run-dir`: target audit-pipeline directory; defaults to `runs/<today>/<HHMMSS>_swmm_anywhere/`.

## Optional inputs

- `--refresh-raw`: re-download OSM/DEM even if a snapshot already exists. Default off — re-runs reuse the cached `00_raw/` snapshot.
- `--project-name`: human-readable label embedded in the manifest.

## Defaults — tuned for fewer, more useful outfalls

The skill ships with `outfall_derivation` parameters tuned in spike 04 (A/B'd against SWMManywhere defaults on the same 1×1 km London Greenwich bbox):

| Parameter | SWMManywhere default | This skill | Effect |
|---|---|---|---|
| `outfall_derivation.method` | `separate` | **`withtopo`** | Outfall ids decided jointly with topology derivation rather than via independent MST. **~34 % fewer outfalls** in the spike test. |
| `outfall_derivation.river_buffer_distance` | 150 m | **300 m** | More street nodes can pair with the same river segment, so sub-networks merge. |
| `outfall_derivation.outfall_length` | 40 | **200** | Stronger penalty against selecting additional outfalls. |

On the spike bbox these defaults dropped outfalls from 50 to 33 (-34 %), grew pipes from 500 to 517 (+3.4 %), and shortened end-to-end runtime from 40 s to 32 s. The defaults can be overridden per call.

## Skill artifacts produced

```
runs/<date>/<id>/
├── 00_raw/                       # raw OSM/DEM/buildings snapshot
│   ├── street.json
│   ├── elevation.tif
│   ├── building.geoparquet
│   ├── river.json
│   └── raw_manifest.json         # SHA-256 of every file + source URLs
├── 10_swmmanywhere/
│   ├── synth.inp                 # the runnable SWMM 5.2 model
│   ├── storm.dat                 # copied alongside (path-with-spaces fix)
│   ├── nodes.geoparquet          # for visualization
│   ├── edges.geoparquet          # for visualization (color by outfall_id)
│   ├── subcatchments.geoparquet  # for visualization
│   └── synth_provenance.json     # parameters, tool versions, timings
```

## Constraints and known limits

- **Apple Silicon (macOS arm64)**: SWMManywhere's `pyswmm` dependency triggers a `SIGKILL` on import because its bundled `swmm.toolkit._solver.abi3.so` ships its own `libomp.dylib` that collides with the OS OpenMP runtime. The runner module stubs `pyswmm` before any SWMManywhere import; aiswmm runs the resulting INP through its own `swmm5` binary, so the stub is fully safe.
- **OSM is mutable**: the same bbox tomorrow can produce a different street graph. The `00_raw/` snapshot pins inputs; treat re-runs without `--refresh-raw` as **byte-identical given the same snapshot**.
- **Plausible ≠ real**: the synthesized network reflects *what a sewer system might look like under these streets and this DEM*. It is a starting point for calibration / sensitivity analysis, not a substitute for surveyed infrastructure data.
- **Memory profile**: end-to-end requires ~1–2 GB free RAM at peak (raster ops + numba JIT). On a constrained machine, close other apps before running large bboxes (> 2×2 km).

## Optional dependency

This skill requires the `aiswmm[anywhere]` optional extra:

```
pip install aiswmm[anywhere]
```

Pulls in 27 geo dependencies (geopandas, osmnx, rasterio, pyflwdir, pywbt, …) — kept out of the default `pip install aiswmm` footprint.

## Upstream attribution

Built on **[ImperialCollegeLondon/SWMManywhere](https://github.com/ImperialCollegeLondon/SWMManywhere)** (BSD-3-Clause), © Imperial College London. Their tooling is the engine for the OSM/DEM ingest and 24-step graphfcn pipeline. The aiswmm `swmm-anywhere` skill wraps it with run-aware audit pipeline integration, raw-input snapshotting, and macOS arm64 portability fixes.
