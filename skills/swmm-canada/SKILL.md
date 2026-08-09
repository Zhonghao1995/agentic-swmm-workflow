---
name: swmm-canada
description: Fetch a ready-to-run SWMM model for any Canadian area from the SWMMCanada upstream service — real published municipal storm pipes where a supported city covers the AOI, synthesized elsewhere in Canada. Input is a bbox or GeoJSON polygon plus a rainfall date window. Use for Canadian locations; outside Canada route to swmm-anywhere instead. Documentation contract only — the runtime tool is the in-process fetch_swmm_from_canada; there is no script or MCP server in this skill.
---

# swmm-canada

**Fetch a ready-to-run SWMM model for a Canadian area from the SWMMCanada upstream service, then run, audit, calibrate, and force it in one canonical run folder.**

Use for any AOI inside Canada. The service auto-selects the build mode: **real published municipal storm networks** where a supported city covers the AOI (35 cities at the 2026-08 sync, e.g. Victoria, Ottawa, Toronto, Calgary, Vancouver, Regina), **synthesized** anywhere else in the country. Outside Canada, route to `swmm-anywhere` (global, synthesized).

This skill is a documentation contract: the implementation is the in-process typed tool `fetch_swmm_from_canada` (a pure-stdlib HTTP client, ADR-0001). There is no script directory and no MCP server here, and that is deliberate — the service boundary is HTTP, and the tool is already on the runtime's golden path.

## What this skill does

Given an AOI (bbox `[min_lon, min_lat, max_lon, max_lat]` or a GeoJSON Polygon string) and a rainfall date window (`start_date`, `end_date`, ISO dates):

1. **Announces a preview** (best effort): which mode and city the service will use.
2. **Submits the build** to the SWMMCanada tasks API and polls with live progress (network fetch, subcatchments, DEM, landcover/soil, climate, build).
3. **Downloads the model bundle** and lands it in the canonical layout (ADR-0004): `model.inp` in `05_builder/` (with a `[REPORT]` section injected when the upstream INP omits one, so the binary output carries per-element series), the full `swmm_model.zip` in `10_upstream/swmmcanada/` as the pristine provenance artifact, and the bundle's returned DATA unpacked into `00_raw/swmmcanada/` (datastore, DEM, land cover, soil rasters, exports) so the run's raw material is browsable beside the other inputs. A study-area map is rendered best-effort to `00_raw/study_area.png` (needs the `gis` extra; the fetch never fails over the map) and is picked up by `swmm-report`'s figures section.
4. **Returns** the INP path, run directory, service URL, task id, build mode, and the upstream validation record.

Validated live end to end: a downtown Victoria AOI produced a real 423-subcatchment network in 173 s, ran under the local `swmm5` unmodified, and audited cleanly; the whole fetch-run-audit chain took 178 s.

## Required inputs

- `aoi_geojson` (GeoJSON Polygon string) **or** `bbox` (four WGS84 floats).
- `start_date`, `end_date`: the rainfall window (`YYYY-MM-DD`). This decides which observed rainfall the service attaches, so changing the window changes the storm.

### Demo AOI (verified live)

When the user names a listed place without coordinates, use the verified
demo AOI DIRECTLY (do not stop to ask first; asking is reserved for the
safe profile):

| Place phrase | bbox `[min_lon, min_lat, max_lon, max_lat]` | Verified |
| --- | --- | --- |
| downtown Victoria, BC | `[-123.370, 48.425, -123.360, 48.432]` | 2026-08-08, 423-subcatchment real-pipe network, fetch-run-audit in 178 s |

State in the result card that the demo AOI was used and that a
project-specific study boundary should replace it for real work. For any
other place name, ask for the bbox (interactive) or say exactly what to
rerun with (single-shot); never guess a study boundary.

## Optional inputs

- `run_dir`: reuse an existing run directory (otherwise a timestamped `runs/agent/swmm-canada-*` is created).
- `infiltration`: `CURVE_NUMBER` (service default), `HORTON`, or `GREEN_AMPT`; passed through verbatim.
- `base_url`: override the service endpoint for one call.

## Configuration

The service URL comes from the `AISWMM_SWMMCANADA_URL` environment variable (a local container at `http://localhost:8000` or a hosted backend). `aiswmm doctor` probes `GET /api/v1/healthz` when the variable is set and reports reachability; unset is a quiet OK because the upstream is optional.

## What to do next (the chain)

Pass the returned `run_dir` and `inp_path` forward so every stage lands in the same run folder:

1. `run_swmm_inp` (agent) or `aiswmm run --inp <inp> --run-dir <run_dir>` (CLI) — simulate.
2. `audit_run` / `aiswmm audit --run-dir <run_dir>` — provenance, QA verdict, diagnostics.
3. `aiswmm calibrate` with observed flow data — the fetched model is an **uncalibrated first-pass estimate**; expect the audit gate to flag routing continuity on raw builds, which is the gate doing its job.
4. `review_run` / `aiswmm review --run-dir <run_dir>` — reference-free design-review of the fetched network (soft rulebook verdicts).
5. `plot_run` — hydrograph figures into the run's canonical plot stage (`08_plot/`), which is where report generation looks.
6. `generate_report` / `aiswmm report --run-dir <run_dir>` — client Word deliverable; embeds the figures plotted in step 5.
7. `run_climate_scenarios` / `aiswmm climate --params-json best_params.json --patch-map <map>` — precipitation-scaled what-ifs on the calibrated model.

## Boundaries

- **Outside Canada**: the tool fails soft with a hint before any HTTP round trip; use `swmm-anywhere`.
- **Real network vs synthesized**: decided server-side per AOI; the result reports which mode ran. Treat both like the synth path for QA purposes (reference-free checks until calibration).
- **Provenance**: aiswmm never re-derives what the service already recorded; the zip in `10_upstream/swmmcanada/` is the durable upstream artifact, with the service URL and task id as foreign keys.
