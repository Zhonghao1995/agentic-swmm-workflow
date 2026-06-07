# Quickstart: rapid SWMM modelling in a data-scarce region via SWMManywhere + aiswmm audit

> **Upstream credit (read this first).** The network *synthesis* in this workflow — turning a bounding box into a plausible drainage network — is performed by **SWMManywhere**, © Imperial College London, BSD-3-Clause (<https://github.com/ImperialCollegeLondon/SWMManywhere>). SWMManywhere is **not** an aiswmm component, and the synthesised network is **not** aiswmm's intellectual output.
>
> aiswmm provides only a wrapper (`swmm-anywhere` skill) that calls SWMManywhere on your behalf, runs the resulting SWMM model through aiswmm's deterministic `swmm5` execution path, and produces an audit dossier. The wrapper does **not** modify SWMManywhere's synthesis algorithm.
>
> **If your work using this workflow produces a publication, the primary citation must be to SWMManywhere.** aiswmm should be cited only as the integration / audit layer.

This page is the *how-to* — installation, the natural-language prompts to drive the workflow, and the audit structure you should expect afterwards. The companion document [`swmm-anywhere-case-study.md`](swmm-anywhere-case-study.md) is the *what-it-produced* evidence record (Greenwich 1 × 1 km bbox, 38 s wall time, peak 354 LPS) with explicit boundaries on what that result proves and does not prove.

**What this workflow is for.** Rapid, *plausibility-based* SWMM modelling for areas where you have no real pipe data. SWMManywhere's output is an inference from OSM + DEM, not a substitute for surveyed infrastructure. If you have real pipe shapefiles, the `swmm-gis` or `swmm-network` aiswmm skills are the correct entry points instead.

## What you will need

### Software prerequisites

* **Python 3.10 or newer** — verified on 3.11.14 (Homebrew, Apple Silicon). System `/usr/bin/python3` on macOS is 3.9 and will not work.
* **aiswmm runtime + SWMM 5.2.4 binary** — install path described in [`installation.md`](installation.md). The `swmm-anywhere` skill expects the runtime's own `swmm5` binary at `/opt/homebrew/bin/swmm5` (macOS) or `/usr/local/bin/swmm5` (Linux/Docker), **not** the `pyswmm` Python wrapper.
* **Network connectivity** — the SWMManywhere pipeline downloads OSM streets, building footprints, DEM tiles, and river lines on **every** call (it re-downloads per run today; cache-aware reuse of `00_raw/` is reserved future work). Each run's downloads are captured and SHA-256-verified under `runs/<id>/00_raw/`, pinning the exact inputs for the record — so plan for network access on every synth run.
* **Disk space** — budget ~500 MB for the optional `[anywhere]` Python dependencies and ~50–200 MB per run-directory (OSM/DEM snapshot + SWMManywhere intermediate artefacts).
* **Peak RAM** — 1–2 GB during the SWMManywhere 24-step graphfcn pipeline (numba JIT + WhiteboxTools flow accumulation). Close background applications on machines with < 8 GB total.

### Upstream tool — SWMManywhere (the actual synthesis engine)

* **Project**: SWMManywhere — *Derive and simulate a sewer network anywhere in the world*
* **Authors / origin**: Imperial College London team — see the SWMManywhere repository contributors list at <https://github.com/ImperialCollegeLondon/SWMManywhere/graphs/contributors>
* **License**: BSD-3-Clause
* **Source / download**: <https://github.com/ImperialCollegeLondon/SWMManywhere>
* **PyPI package**: <https://pypi.org/project/swmmanywhere/> (this is what `pip install aiswmm[anywhere]` pulls in)
* **Project documentation**: <https://imperialcollegelondon.github.io/SWMManywhere/>
* **Citation**: please cite SWMManywhere as the **primary** tool if your work depends on the synthesised network. The aiswmm wrapper around it is integration plumbing, not a re-implementation.

The aiswmm `swmm-anywhere` skill **does not vendor** SWMManywhere source code — it pulls the package from PyPI whenever a user runs `pip install aiswmm[anywhere]`. The wrapper layer (in `agentic_swmm/integrations/swmmanywhere_runner.py`) only handles audit-pipeline integration, raw-input snapshotting, macOS arm64 portability, and parameter defaults; it does not contain a copy of, or a modified version of, the synthesis algorithm itself.

## Step 1 — Install the optional `[anywhere]` extra

```bash
# inside whatever Python 3.10+ environment already has aiswmm
pip install "aiswmm[anywhere]"
```

What this brings in (27 packages, ~500 MB):

* `swmmanywhere>=0.2.2,<0.3` — the core synthesis engine from Imperial College London.
* Geospatial stack required by SWMManywhere — `geopandas`, `osmnx`, `rasterio`, `pyflwdir`, `pywbt` (WhiteboxTools wrapper), `xarray`, `netcdf4`, `shapely`, `networkx>=3`, `pyproj`, `pyogrio`, plus their transitive dependencies.
* `pyswmm` is also pulled in by SWMManywhere but is **stubbed** at import-time by aiswmm so it never actually runs (see "Known limits" below for why).

Verify the install:

```bash
aiswmm doctor
# expected: row "swmm-anywhere extra — installed (SWMManywhere by Imperial College London, BSD-3)"
```

If `aiswmm doctor` instead shows `WARN  swmm-anywhere extra — not installed`, the install did not place the package into the same Python environment as the aiswmm CLI. Resolve by checking `which aiswmm` and `python -m pip show aiswmm`.

## Step 2 — Configure an LLM provider (one-time, for natural-language entry)

The deterministic chain works without any LLM call (see Step 4 fallback). To get the *natural-language* entry, configure one of:

```bash
# Option A — OpenAI
export OPENAI_API_KEY="sk-…"
aiswmm setup --provider openai

# Option B — Claude Pro / Max via the local `claude` CLI
claude login
aiswmm setup --provider claude_sdk    # requires AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS=1
```

`aiswmm doctor` should now report at least one provider as `present`.

## Step 3 — Recommended natural-language prompt sequence

Launch the interactive runtime:

```bash
aiswmm
```

The four-prompt sequence below is the recommended walkthrough; it mirrors the case-study run and exercises every part of the synth-data path. Replace the bbox with one of your own when you want to try a different region.

### Prompt 1 — ask SWMManywhere (via aiswmm) to synthesise a plausible network

```
我要给伦敦 Greenwich 区一个 1×1 km 的范围建一个 SWMM 模型，没有真实管网数据。
bbox 是 [0.04020, 51.55759, 0.05450, 51.56660]。请你用 SWMManywhere 从 OSM + DEM 推一个 plausible 的管网。
```

What the planner does:

1. Reads `intent_map.json` → matches the `synth-from-bbox` intent (`exclusive_when: user has not provided any .shp/.csv/network.json/.inp file`).
2. Confirms the bbox interpretation back to you and asks for an output directory (or accepts the default `runs/<date>/<HHMMSS>_swmm_anywhere/`).
3. Calls `skills/swmm-anywhere/scripts/synth_from_bbox.py` with the bbox. That script in turn calls **SWMManywhere** (Imperial College London, BSD-3) to perform the actual OSM/DEM download, street-graph cleanup, subcatchment delineation, pipe topology derivation, and pipe sizing.

Expected end-state (the contents of `10_swmmanywhere/` are SWMManywhere's outputs, not aiswmm's):

* `runs/<date>/<id>/10_swmmanywhere/synth.inp` — the SWMM 5.2 model written by SWMManywhere (~540 KB on a 1 × 1 km bbox).
* `runs/<date>/<id>/10_swmmanywhere/{nodes,edges,subcatchments}.geoparquet` — SWMManywhere's intermediate geometries.
* `runs/<date>/<id>/10_swmmanywhere/synth_provenance.json` — record of which SWMManywhere parameters and version were used.
* `runs/<date>/<id>/00_raw/raw_manifest.json` — the aiswmm wrapper's contribution: SHA-256 of every OSM/DEM file SWMManywhere fetched, so a re-run can be byte-identical.
* Console reports total wall time (~30–40 s on Apple Silicon).

### Prompt 2 — run SWMM with aiswmm's own swmm5 binary

```
跑这个 synth.inp，用你自己的 swmm5 不要 pyswmm，然后审计这个 run。
```

What the planner does:

1. Matches the `runner` intent → `aiswmm run --inp <synth.inp> --run-dir <run>/swmm_run`.
2. After the simulation completes (typically 2–4 s), matches the `audit` intent → `aiswmm audit --run-dir <run>/swmm_run`.

Expected artefacts under `swmm_run/`:

* `05_runner/model.rpt` (~350 KB), `05_runner/model.out` (~3.7 MB)
* `06_qa/runner_peak.json`, `06_qa/runner_continuity.json`
* `09_audit/experiment_provenance.json` (14 tracked artefacts)
* `09_audit/experiment_note.md` (Markdown audit dossier, see Step 5)
* `09_audit/comparison.json`, `09_audit/model_diagnostics.json`

### Prompt 3 — render the spatial network layout

```
给我看一下这个网络长什么样，画一张布局图。
```

What the planner does:

1. Matches the `map` intent (added in v0.7.1) → calls `aiswmm map --run-dir <run> --out-png <run>/swmm_run/07_plots/network_map.png`.
2. The `map` command auto-discovers SWMManywhere's geoparquet trio under `10_swmmanywhere/` and renders subcatchment polygons + conduits coloured by outfall + outfall stars + nodes.

Expected output: a PNG roughly equivalent to [`figs/swmm_anywhere_network_map.png`](figs/swmm_anywhere_network_map.png) in this repo.

### Prompt 4 — peak-flow hydrograph for one conduit

```
RPT 里面 peak 流量最大的 conduit 是哪个？给我画那一条以及它上游 3 段的 hydrograph，
范围 00:00 到 02:00。
```

This is where the **HITL pattern in `skills/swmm-plot/SKILL.md`** kicks in. The planner does **not** silently pick defaults; it inspects the RPT `Link Flow Summary`, lists the top 3–5 candidate conduits with their peak values + times, and asks you to confirm before it draws anything. After you confirm, it calls `aiswmm plot --run-dir <run>/swmm_run --link <id> --window-start 00:00 --window-end 02:00`.

Expected output: a two-panel PNG with conduit flow on top and the 15-minute storm on the bottom, structurally identical to [`figs/swmm_anywhere_conduit_chain.png`](figs/swmm_anywhere_conduit_chain.png).

### Variations

You can compress the 4 prompts into one if you want one-shot behaviour:

```
我有伦敦 Greenwich 区 1×1 km 的 bbox [0.04020, 51.55759, 0.05450, 51.56660] 但没有管网数据。
请帮我合成一个 SWMM 模型，跑 24 小时，做 audit，画网络图和峰值 conduit 的水文图。
```

Single-prompt mode trades the HITL plot interaction for speed — the planner will pick the highest-peak conduit automatically and explain its choice in the response.

## Step 4 — Deterministic chain (if you skip the LLM)

The same chain runs end-to-end without natural-language entry, in case you have no provider configured or want a CI-style reproducible script:

```bash
python scripts/spike_swmmanywhere/05_e2e_chain.py
```

This is the literal script used to produce the case-study figures. The bbox is hard-coded to the Greenwich values; edit them in-place to change region. It will:

1. Stub `pyswmm` in `sys.modules` (works around the macOS arm64 OpenMP collision).
2. Call `run_synth_from_bbox(bbox=…, run_dir=…)` from the runner module.
3. Shell out to `aiswmm run --inp …`, `aiswmm audit --run-dir …`, `aiswmm plot --run-dir …`.
4. Parse `model.rpt` for peak flow and report it.

Total wall time: ~38 s on the spike machine.

## Step 5 — Audit structure walk-through

The `09_audit/` directory is the centrepiece of what aiswmm adds on top of raw SWMManywhere. Below is what each file contains and how to read it.

### `experiment_note.md` — start here

A self-contained Markdown report intended for direct reading in Obsidian (which is why aiswmm's audit pipeline is Obsidian-compatible). Its sections, in order:

1. **YAML frontmatter** — `type`, `project`, `run_id`, `status` (`ok` / `warn` / `fail`), `created_at_utc`, `tags`.
2. **Executive Summary** — one paragraph in plain English summarising peak / continuity / status. Example from the case study: *"The recorded peak flow is 353.96 LPS at outfall 119 at simulation time 00:21."*
3. **Run Identity** — INP SHA-256, sim duration, run_dir absolute path, model `[OPTIONS]` (FLOW_UNITS, INFILTRATION, FLOW_ROUTING).
4. **Continuity Balance** — table of routing continuity errors. Anything > 5 % is flagged.
5. **Peak Flow** — node and link peaks parsed from RPT.
6. **QA Gates** — the runtime's hard checks (peak missing? continuity exceeded? no outfalls? etc.).
7. **Known Limitations** — for synth runs this section auto-notes the OSM data version, the bbox, and that the network is inferred not measured.

### `experiment_provenance.json` — what files contributed to the run

JSON with one entry per tracked artefact. Each entry:

```json
{
  "path": "swmm_run/05_runner/model.rpt",
  "sha256": "…",
  "size_bytes": 359142,
  "role": "swmm_runner_report",
  "schema_version": "1.0"
}
```

The file is **append-only**: once an audit pass writes it, subsequent passes never overwrite. Re-auditing a run produces a new file under `09_audit/audit-<timestamp>/` and leaves the original.

### `manifest.json` — INP traceability

Three SHA-256 fingerprints proving that the INP run by `swmm5` is the same file SWMManywhere wrote:

* `source_inp.sha256` — what SWMManywhere produced under `10_swmmanywhere/synth.inp`
* `builder_inp.sha256` — what the runner staged into `04_builder/model.inp`
* `run_inp.sha256` — what swmm5 actually consumed at `00_inputs/model.inp`

For the case-study run, all three matched: `a02839079c576f82e837885afd47692211692629208d50d38d1d83f59dad5247`.

### `comparison.json` — empty for synth-only runs

Populated only when you supply `--baseline-run-dir` to `aiswmm audit`. Slot reserved for future synth-vs-real comparisons.

### `model_diagnostics.json` — quick model-quality read

Synth-network-specific diagnostics:

```json
{
  "subcatchment_count": 494,
  "outfall_count": 33,
  "total_pipe_length_m": 12345.6,
  "flooding_fraction_of_inflow": 0.40,
  "continuity_error_percent": 5.45
}
```

A real-data run would have the same schema; the diagnostics are network-agnostic.

## Step 6 — Verify your reproduction matches expectations

For exact reproducibility against the case study you need the same `00_raw/` snapshot — which would require us to publish the case-study snapshot as a release asset (planned for v0.7.1 release). For now, verify structural reproducibility:

| Check | Expected (Greenwich 1 × 1 km) |
| --- | --- |
| `synth.inp` size | 500–600 KB |
| Subcatchment count | 400–500 |
| Conduit count | 450–550 |
| Outfall count | 30–40 (with default tuned `outfall_derivation`); ~50 with `--upstream-defaults` |
| swmm5 24h simulation | 1–4 s wall time, no `ERROR` lines in RPT |
| Peak flow | 100–500 LPS at one of the larger outfalls |
| `aiswmm map` output | PNG, 150–300 KB, shows clusters of coloured sub-networks |

If your numbers fall outside these ranges and you used the default tuned parameters, the most likely cause is OSM data drift; check `00_raw/raw_manifest.json` for the capture timestamp.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `error: swmm-anywhere stage 'extra_missing' failed` | `pip install aiswmm[anywhere]` was not run, or was run into a different Python env | `pip install aiswmm[anywhere]` into the same env as `aiswmm`. `which python` should match `which aiswmm`'s shebang. |
| `SIGKILL` immediately after starting | Out-of-memory on a small Mac, or `libomp` collision on macOS arm64 from an unrelated import order | Close background apps. If still failing, edit your driver to install the `pyswmm` stub *before* any `swmmanywhere` import — see `agentic_swmm/integrations/swmmanywhere_runner.py:_install_pyswmm_stub`. |
| `ERROR 205: invalid keyword … of [RAINGAGES] section` | The synth INP references an absolute path containing spaces (a known SWMM 5.2 parser bug) | Already mitigated by `normalize_external_paths` — if you see this in a non-spike code path, file an issue. |
| `aiswmm plot` fails with `Unable to infer rainfall TIMESERIES` | aiswmm v0.7.0 or older, which only parses `[TIMESERIES]` rainfall, not `[RAINGAGES] FILE` (SWMManywhere's format) | Upgrade to v0.7.1+ — the FILE-fallback parser ships there. |
| `aiswmm map` says `geopandas not available` | `[anywhere]` extra not installed; the INP-text-parsing fallback should engage automatically | If `--inp` is set and the geoparquet trio is absent, the map verb falls back to pure-matplotlib INP parsing. If you see this message and have geoparquet files in `10_swmmanywhere/`, file a bug. |

## What this workflow does NOT do (important limits)

The case study's *evidence boundary* section enumerates the limits in full. To repeat the most important ones for your reproduction planning:

* The synthesised network is **plausible, not real**. SWMManywhere infers pipe locations from OSM street geometry plus DEM flow direction; the result is *what a sewer system might look like under these streets and this DEM*, **not** a surveyed infrastructure dataset. Do not present the output as a measured network in any publication.
* This workflow is **not** a re-implementation, replacement, or improvement of SWMManywhere. The aiswmm wrapper adds integration plumbing only; the modelling algorithm is unchanged from SWMManywhere upstream. Any claims about modelling capability belong to SWMManywhere.
* It does **not** validate against real measurements. No observed flows are compared with the synth output here; doing so requires a real reference network, which by definition does not exist for the workflow's target use cases.
* It does **not** guarantee cross-environment byte-identical results (unlike the v0.6.4 Tecnopolo run). OSM drift is a permanent source of non-determinism; snapshot pinning under `00_raw/` is a best-effort defence.
* It does **not** auto-calibrate. If you have observed flows for the bbox, the synth INP becomes a *starting point* for `aiswmm calibrate`, not a final model.
* It does **not** ship in the default (thin) Docker image. The `[anywhere]` extra ships as a separate **companion image**, `ghcr.io/zhonghao1995/agentic-swmm-workflow:<tag>-anywhere`. The `0.7.1-anywhere` variant is **published** — `docker pull ghcr.io/zhonghao1995/agentic-swmm-workflow:0.7.1-anywhere`. Companion images are built manually per release (GitHub Actions → "Docker (anywhere variant)"), so a given tag has an `-anywhere` image only if it was explicitly built.

## Where the case study run lives

* Document: [`docs/swmm-anywhere-case-study.md`](swmm-anywhere-case-study.md)
* Figures: [`docs/figs/swmm_anywhere_network_map.png`](figs/swmm_anywhere_network_map.png), [`docs/figs/swmm_anywhere_conduit_chain.png`](figs/swmm_anywhere_conduit_chain.png), [`docs/figs/swmm_anywhere_rain_runoff.png`](figs/swmm_anywhere_rain_runoff.png)
* Deterministic driver: [`scripts/spike_swmmanywhere/05_e2e_chain.py`](../scripts/spike_swmmanywhere/05_e2e_chain.py)
* SKILL doc: [`skills/swmm-anywhere/SKILL.md`](../skills/swmm-anywhere/SKILL.md)
* Wrapper module: [`agentic_swmm/integrations/swmmanywhere_runner.py`](../agentic_swmm/integrations/swmmanywhere_runner.py)
