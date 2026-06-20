---
name: swmm-anywhere
description: Synthesize a plausible SWMM drainage network from public data (OSM streets + DEM) when NO real pipe-network data exists — input is just a bbox. Use ONLY when the user has no pipe shapefile/CAD/GIS data, or to establish a baseline before real data arrives; if real pipe data exists, route to swmm-network or swmm-gis instead.
---

# swmm-anywhere

**Synthesize a plausible SWMM drainage network from public data (OSM streets + DEM) when no real pipe-network data exists.**

Use **ONLY** when the user explicitly does not have pipe shapefile / CAD / GIS data, or when establishing a baseline before real data arrives. **Do NOT use if `swmm-gis` or `swmm-network` can run on the user's actual data** — the synthesized network is an *inferred plausibility*, not measured infrastructure.

For bbox-only inputs without real pipe data: this is the right skill.
For inputs that include a `.shp`, `.csv`, or `network.json` of real pipes: route to `swmm-network` or `swmm-gis` instead.

## What this skill does

Given a bounding box (and optional region name), this skill:

1. **Downloads public source data** via SWMManywhere: OpenStreetMap streets, a DEM tile (Planetary Computer by default), building footprints, river lines.
2. **Snapshots the raw inputs** under `runs/<date>/<id>/00_raw/` with a SHA-256 manifest that is **verified after capture** (the result lands in `synth_provenance.json` under `raw_snapshot_verified`), so the exact OSM/DEM inputs that produced this run are pinned and audited (OSM/DEM otherwise drift continuously upstream).
3. **Runs SWMManywhere's 24-step graph pipeline** to infer subcatchment polygons, manhole nodes, pipe topology, pipe diameters, and outfall locations.
4. **Writes a SWMM 5.2 `.inp`** under `runs/<date>/<id>/10_swmmanywhere/synth.inp`, post-processed so the aiswmm `swmm5` binary can run it directly (external `storm.dat` is copied next to the INP and its path is rewritten as relative, dodging the macOS path-with-spaces parsing bug).
5. **Returns** the INP path, raw-snapshot manifest path, and a structured provenance record (which graphfcns ran, parameter overrides used, upstream tool versions).

The synthesized INP is **immediately runnable** through `swmm-runner` and **immediately auditable** through `swmm-experiment-audit`.

## Required inputs

- `--bbox`: four floats `min_lon min_lat max_lon max_lat` (WGS84). 1×1 km is a comfortable test size; smaller is faster and uses less RAM, larger needs more.
- `--run-dir`: target audit-pipeline directory; defaults to `runs/<today>/<HHMMSS>_swmm_anywhere/`.

## Optional inputs

- `--refresh-raw`: reserved flag for a future cache-aware path. **Today every call re-downloads** OSM/DEM via SWMManywhere's own `prepare_data`; aiswmm does not yet replay a run *from* the `00_raw/` snapshot, so this flag has no effect at the aiswmm layer yet.
- `--project-name`: human-readable label embedded in the manifest.
- `--config-overrides`: JSON object of per-call SWMManywhere parameter overrides to adjust the synthesis (cure orphan nodes, tune pipe density). See *Adjusting the synthesis* below. The `synth_swmm_from_bbox` agent tool exposes the same as a `config_overrides` argument.

## Defaults — tuned for fewer, more useful outfalls

The skill ships with `outfall_derivation` parameters tuned in spike 04 (A/B'd against SWMManywhere defaults on the same 1×1 km London Greenwich bbox):

| Parameter | SWMManywhere default | This skill | Effect |
|---|---|---|---|
| `outfall_derivation.method` | `separate` | **`withtopo`** | Outfall ids decided jointly with topology derivation rather than via independent MST. **~34 % fewer outfalls** in the spike test. |
| `outfall_derivation.river_buffer_distance` | 150 m | **300 m** | More street nodes can pair with the same river segment, so sub-networks merge. |
| `outfall_derivation.outfall_length` | 40 | **200** | Stronger penalty against selecting additional outfalls. |

On the spike bbox these defaults dropped outfalls from 50 to 33 (-34 %), grew pipes from 500 to 517 (+3.4 %), and shortened end-to-end runtime from 40 s to 32 s. The defaults can be overridden per call.

## Adjusting the synthesis — orphan nodes, too many / too few pipes

When the structural QA step (`network_qa.py --inp ...`, step 1 of *What to do next*) flags `isolated_node` / `no_outfall_path` nodes, or the pipe count looks off, tune SWMManywhere's parameters and re-synthesise. Pass overrides per call — CLI `--config-overrides '<json>'`, or the `synth_swmm_from_bbox` tool's `config_overrides` argument — shape `{group: {param: value}}`. The runner merges them onto the resolved config and re-runs the pipeline.

| Symptom | Knob (`group.field`) | Default | Turn it |
|---|---|---|---|
| Orphan / `no_outfall_path` nodes | `outfall_derivation.outfall_length` | **200** | **Lower** toward the upstream 40 → more outfalls, fewer orphans. This skill's tuned 200 *suppresses* outfalls (see Defaults above), so it is the first suspect for orphan nodes. |
| Orphan nodes | `outfall_derivation.river_buffer_distance` | 300 | Raise (≤ 500) → streets pair with a river segment more easily. |
| Orphan nodes (quick reset) | *all three outfall defaults* | — | Pass `upstream_defaults: true` (tool) / `--upstream-defaults` (CLI) to drop aiswmm's tuned overrides and use SWMManywhere's `separate` / 150 / 40 in one switch. |
| Too many pipes | `subcatchment_derivation.node_merge_distance` | 10 | **Raise** (≤ 39.9, must stay `< max_street_length`) → merges nearby nodes → fewer pipes. |
| Too many pipes | `subcatchment_derivation.max_street_length` | 60 | Raise (≤ 100) → fewer street segments → fewer pipes. |
| Too few pipes | the two above | — | Lower them; or add a type to `topology_derivation.allowable_networks` (default `[walk, drive]`). |

Example — more outfalls (cure orphans) **and** fewer pipes:

```bash
python3 skills/swmm-anywhere/scripts/synth_from_bbox.py --bbox <min_lon> <min_lat> <max_lon> <max_lat> \
  --config-overrides '{"outfall_derivation": {"outfall_length": 60}, "subcatchment_derivation": {"node_merge_distance": 25}}'
```

SWMManywhere is nonlinear, so expect 1–2 iterations to hit a target, and keep each value inside its valid range (synthesis errors on out-of-bounds, e.g. `node_merge_distance` ≥ `max_street_length`). Re-run the structural QA after each re-synthesis to confirm the fix.

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

## Example invocation

Minimum: just give a bbox.

```bash
# Default — uses tuned outfall_derivation defaults (33 outfalls on the spike bbox)
python skills/swmm-anywhere/scripts/synth_from_bbox.py \
    --bbox 0.04020 51.55759 0.05450 51.56660 \
    --run-dir runs/2026-05-28/100000_my_first_synth
```

To reproduce SWMManywhere upstream extended_demo behaviour (separate-mode outfalls):

```bash
python skills/swmm-anywhere/scripts/synth_from_bbox.py \
    --bbox 0.04020 51.55759 0.05450 51.56660 \
    --upstream-defaults \
    --run-dir runs/2026-05-28/100000_upstream_replica
```

To use your own rainfall instead of the bundled 15-min demo storm:

```bash
python skills/swmm-anywhere/scripts/synth_from_bbox.py \
    --bbox 0.04020 51.55759 0.05450 51.56660 \
    --rain-file /path/to/your/storm.dat \
    --run-dir runs/2026-05-28/100000_custom_rain
```

## What to do next

The skill produces a runnable SWMM .inp under `<run-dir>/10_swmmanywhere/synth.inp`. Chain it through aiswmm's standard audit pipeline:

```bash
# 1. Structural QA of the synthesized network (NO SWMM run needed) — flags
#    orphan/isolated nodes, junctions with no downstream path to an outfall,
#    and pipe/outfall counts straight off the synthesized INP. This is the
#    signal for whether to adjust SWMManywhere parameter overrides and
#    re-synthesize before spending a SWMM run on a structurally broken network.
python3 skills/swmm-network/scripts/network_qa.py --inp <run-dir>/10_swmmanywhere/synth.inp

# 2. Run SWMM (aiswmm's own swmm5 binary, NOT pyswmm)
aiswmm run --inp <run-dir>/10_swmmanywhere/synth.inp --run-dir <run-dir>/swmm_run

# 3. Audit the run
aiswmm audit --run-dir <run-dir>/swmm_run

# 4. Plausibility-review the synthesized model (NO observed data needed).
#    Reference-free: scores velocity / capacity / slope / roughness / diameter
#    against physical-plausibility bands → <run-dir>/swmm_run/09_review/.
#    Every finding is a WARN (flag-for-review); continuity stays the postflight gate's job.
aiswmm review --run-dir <run-dir>/swmm_run \
  --rules skills/swmm-design-review/rulebooks/synth_plausibility.yaml

# 5. Plot rain/runoff
aiswmm plot --run-dir <run-dir>/swmm_run

# 6. Plot a specific node or conduit (requires --link support; see swmm-plot SKILL.md)
aiswmm plot --run-dir <run-dir>/swmm_run --node <node_id> --node-attr Total_inflow

# 7. Pick a peak-flow conduit from the RPT Link Flow Summary and plot it
aiswmm plot --run-dir <run-dir>/swmm_run --link <conduit_id>
```

## Constraints and known limits

- **Apple Silicon (macOS arm64)**: SWMManywhere's `pyswmm` dependency triggers a `SIGKILL` on import because its bundled `swmm.toolkit._solver.abi3.so` ships its own `libomp.dylib` that collides with the OS OpenMP runtime. The runner module stubs `pyswmm` before any SWMManywhere import; aiswmm runs the resulting INP through its own `swmm5` binary, so the stub is fully safe.
- **OSM is mutable**: the same bbox tomorrow can produce a different street graph. The `00_raw/` snapshot pins and SHA-256-verifies the exact inputs each run used, so a run stays **auditable against upstream drift**. (Replaying a *new* run from an existing snapshot — true byte-identical re-synthesis — is reserved future work; today each call re-downloads, so two runs of the same bbox can differ if OSM changed between them.)
- **Plausible ≠ real**: the synthesized network reflects *what a sewer system might look like under these streets and this DEM*. It is a starting point for calibration / sensitivity analysis, not a substitute for surveyed infrastructure data.
- **Memory profile**: end-to-end requires ~1–2 GB free RAM at peak (raster ops + numba JIT). On a constrained machine, close other apps before running large bboxes (> 2×2 km).

## Installing — and credit where it's due

This skill leans on **[SWMManywhere](https://github.com/ImperialCollegeLondon/SWMManywhere)**, a project from the Imperial College London team that figures out a plausible urban drainage network from OSM streets and a DEM. The clever part — graph cleanup, subcatchment delineation, pipe topology, pipe sizing — is all their work, released under BSD-3-Clause. What this skill adds is the agent-loop plumbing around it: a typed tool the LLM can call, a SKILL.md contract for context priming, a Python runner that smooths over a few macOS arm64 quirks, and the standard `runs/<date>/<id>/` audit layout for the resulting INP.

To install:

```
pip install aiswmm[anywhere]
```

That brings in `swmmanywhere` from PyPI along with the geospatial stack it needs (geopandas, osmnx, rasterio, pyflwdir, pywbt, and ~22 others). The default `pip install aiswmm` stays light — the geo stack only shows up when you opt in to this extra.

If you're publishing work that uses or builds on this skill, please **cite SWMManywhere** and check the upstream repository at <https://github.com/ImperialCollegeLondon/SWMManywhere> for their citation guidance and the BSD-3-Clause license text. The synthesised network is upstream's intellectual contribution; this skill is just the agent-side adapter.
