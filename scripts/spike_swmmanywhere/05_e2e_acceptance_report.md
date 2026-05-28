# E2E chain acceptance — spike 05

**Timestamp:** 2026-05-27 19:54 PT (run dir `runs/2026-05-27/195456_e2e_chain/`)
**Branch / commit:** `feat/swmmanywhere` / `d86214c`
**Script:** [`scripts/spike_swmmanywhere/05_e2e_chain.py`](./05_e2e_chain.py)
**Hypothesis:** aiswmm can drive SWMManywhere end-to-end:
bbox → synth SWMM model → run with aiswmm's `swmm5` → audit → plot → peak.

## TL;DR

Chain green. 38 s wall-clock from bbox to peak. 1×1 km London Greenwich
synth model produces a 33-outfall network; peak `Max_Flow` = **353.96 LPS
at `384_outfall` @ day 0 00:15** under the SWMManywhere-bundled `storm.dat`
(3-pulse storm). Audit artefacts complete; plot renders rain + flow at the
peak outfall.

The "data-scarce, natural-language → SWMM model + audit" story is now
provable as a deterministic pipeline. The LLM-driven path (Phase 3) was
skipped — no provider configured (see below).

## Phase 1+2: deterministic chain (5 steps)

All commands run from project root (`/Users/zhonghao/Desktop/Codex Project/Agentic SWMM`).
Step 5 was executed before step 4 so the peak outfall could be passed as
`aiswmm plot --node`; table is sorted by chain order.

| Step | Tool                                        | Status | Time     | Output                                                                                       |
|------|---------------------------------------------|--------|----------|----------------------------------------------------------------------------------------------|
| 1    | `synth_from_bbox.py` (skill, spike venv)    | OK     | 33.61 s  | `10_swmmanywhere/synth.inp` (534.1 KB), `00_raw/` snapshot, `synth_provenance.json`           |
| 2    | `aiswmm run --inp ... --run-dir ...`        | OK     |  2.77 s  | `swmm_run/05_runner/model.rpt` (350.7 KB), `model.out` (3710.9 KB); swmm5 5.2.4, return_code=0 |
| 3    | `aiswmm audit --run-dir <swmm_run> ...`     | OK     |  0.23 s  | `09_audit/experiment_provenance.json` (14 artefacts indexed), `experiment_note.md`, `comparison.json`, `model_diagnostics.json` |
| 4    | `aiswmm plot --run-dir ... --inp model_plot.inp --node 384_outfall --rain-ts storm` | OK | 1.42 s | `07_plots/fig_rain_runoff.png` (94.3 KB) |
| 5    | RPT parse (Outfall Loading + Node Inflow)   | OK     |  0.00 s  | peak 353.96 LPS at `384_outfall` (within Outfall Loading Summary)                            |
|      | **Total**                                   |        |**38.03 s**|                                                                                              |

## Peak flow result

- **Value:** 353.96 LPS (= 0.354 m³/s)
- **Time:** day 0, 00:15 (relative to model start 2000-01-01 00:00)
- **Node:** `384_outfall` (`OUTFALL` type; upstream junction `384` carries 355.14 LPS)
- **Context:** 1×1 km London Greenwich, 33 outfalls, SWMManywhere `storm.dat` (4 pulses at 5-min spacing: 0/28/32/3 mm/5min), DYNWAVE routing.
- **Top 3 outfalls by peak Max_Flow:**
  - `384_outfall`: 353.96 LPS
  - `1086_outfall`: 140.03 LPS
  - `119_outfall`: 134.37 LPS

## Audit artefacts (Step 3)

`runs/2026-05-27/195456_e2e_chain/swmm_run/09_audit/`:

- **`experiment_provenance.json`** — 14 artefacts tracked (builder/runner manifests, RPT, OUT, INP hash, QA summary, model diagnostics …). `case_name=greenwich_e2e_chain`, `workflow_mode=synthetic_bbox`, `objective=e2e_chain_acceptance` all captured from the audit CLI args.
- **`experiment_note.md`** — human-readable summary (note: status is `fail` because flow-routing continuity = 5.449% exceeds the QA gate; the chain still completed, the failure flag is a model-quality finding, not a chain failure).
- **`comparison.json`**, **`model_diagnostics.json`**.

## Plot artefact (Step 4)

`07_plots/fig_rain_runoff.png` — rainfall hyetograph + flow time series at
the peak outfall, rendered cleanly. SWMManywhere INPs reference rainfall
via `RAINGAGES FILE storm.dat` and ship no in-INP `[TIMESERIES]`, which
`aiswmm plot` requires. Workaround in the spike script: synthesise a
sidecar `00_inputs/model_plot.inp` that inlines storm.dat values as a
`[TIMESERIES]` named `storm`, leaving the run-of-record INP untouched.
Plot then runs with `--inp model_plot.inp --rain-ts storm`.

**Plot-tool observation (non-blocking):** the right-side y-axis labels
read `Flow (m³/s)` but the model is in `LPS`. The orange flow line peaks
at ~354 (LPS, matches RPT), not at 354 m³/s. The plot tool is showing the
raw value with a CMS axis label — minor cosmetic bug in unit handling
when source units are LPS. RPT values remain authoritative.

## Phase 3: natural-language path

- **Attempted?** No.
- **Reason:** `aiswmm doctor` reports both `OPENAI_API_KEY` and Claude Code
  OAuth absent. No LLM provider available, so the chat-driven planner
  cannot be exercised on this machine.
- **Note for future:** v0.7.0 + commit `d86214c` ships the `swmm-anywhere`
  skill on disk, but the LLM planner's intent → skill map almost
  certainly does not yet route a "data-scarce bbox" intent to it
  (`swmm-anywhere` is a Phase-2/3 addition to the PRD, post-v0.7.0).

## Issues / next steps

1. **(Polish) `aiswmm plot` should auto-handle `RAINGAGES FILE`.** Today
   it requires an in-INP `[TIMESERIES]`. SWMManywhere always emits
   FILE-based rainfall — every synthetic model will need the sidecar
   workaround that lives in the spike script. Either: (a) add a
   `--rain-file storm.dat` flag to `aiswmm plot`, or (b) let the plot
   tool transparently materialise a TIMESERIES from a `RAINGAGES FILE`.
2. **(Polish) Unit handling for LPS.** When `Flow Units = LPS` in the
   RPT, the plot's y-axis label currently says `m³/s` while the line is
   in LPS. Either convert or label.
3. **(Wire) Register `swmm-anywhere` in the LLM planner's intent map.**
   Once a provider is configured, a prompt like *"I have a bbox in
   London Greenwich (0.0402, 51.55759, 0.0545, 51.5666) but no pipe
   data"* should route to `swmm-anywhere → swmm-runner → audit → plot`.
   Today the planner doesn't know this skill exists (PRD Phase 4 work).
4. **(Quality) Continuity error 5.45% in flow routing.** The synth
   network's pipe sizing isn't quite balanced — meaningful flooding loss
   (1.82 mm of 4.55 mm wet-weather inflow). Acceptable for the spike
   demo; a real-deployment skill would either run spike 04's overrides
   pass or surface the gate violation to the user.
5. **(Robustness) The spike script sets `PYTHONPATH=<repo_root>` to let
   the spike-venv python find `agentic_swmm`.** When `aiswmm[anywhere]`
   is properly installed into the main venv (PRD Phase 5), this hack
   goes away and the synth script can run against the same interpreter
   as `aiswmm run`.

## Reproduce

```sh
# from project root, main python (any 3.11):
python3.11 scripts/spike_swmmanywhere/05_e2e_chain.py
```

Each run lands under `runs/<YYYY-MM-DD>/<HHMMSS>_e2e_chain/`. The
machine-readable report (`e2e_chain_report.json`) records all step
timings, errors, manifests and the parsed peak.
