---
name: swmm-plot
description: Publication-grade plotting for SWMM rainfall–runoff figures. Use when Zhonghao asks to produce figures from SWMM INP/OUT with strict style rules (SI units, inverted rainfall axis, ticks inward, Arial 12, no title, day/window focus like 09:00–15:00) and to expose plotting as an MCP tool.
---

# SWMM Plot (publication spec)

## What this skill provides
- A plotting script that reads:
  - rainfall TIMESERIES from `.inp`
  - flow series from `.out` (via `swmmtoolbox`)
- Produces figures with Zhonghao’s style spec:
  - SI units; rain shown as **mm/5min** by default
  - inverted rain axis
  - ticks inward
  - Arial 12
  - no title
  - optional x-axis focus: day or time window (e.g., 09:00–15:00)

## Scripts
- `scripts/plot_rain_runoff_si.py`

## MCP
This skill’s scripts are designed to be called by an MCP server (optional).
