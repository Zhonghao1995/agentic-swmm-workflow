---
name: swmm-runner
description: Run EPA SWMM (swmm5) simulations reproducibly and extract key metrics. Use when Zhonghao asks to (1) run a .inp via swmm5 CLI, (2) generate a run directory with rpt/out + manifest, (3) extract peak flow/time for a node/outfall, (4) parse SWMM continuity (Runoff Quantity / Flow Routing) errors from .rpt, or (5) compare GUI vs CLI runs for equivalence.
---

# SWMM Runner (CLI-first)

## What this skill provides
- Deterministic execution wrapper around `swmm5`
- Standard **run directory** layout (inp/rpt/out/stdout/stderr/manifest.json)
- Metric extraction:
  - peak flow + time-of-peak (from `.rpt`)
  - continuity tables + continuity error % (from `.rpt`)

## Scripts
- `scripts/swmm_runner.py`
  - `run` → run swmm5 and write manifest
  - `peak` → parse peak flow/time from rpt
  - `continuity` → parse continuity from rpt
  - `compare` → compare two rpt files (GUI vs CLI)

## Notes / conventions
- Prefer **reading SWMM’s own `.rpt`** for continuity and peak metrics (don’t re-implement physics).
- Keep units explicit in the INP (SI preferred: `FLOW_UNITS CMS`).
