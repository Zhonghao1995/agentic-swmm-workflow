---
name: swmm-climate
description: Deterministic rainfall/climate formatting for SWMM. Use when converting timestamped rainfall CSV files into SWMM-ready [TIMESERIES] lines and [RAINGAGES] helper snippets for swmm-builder.
---

# SWMM Climate (MVP rainfall layer)

## What this skill provides
- Deterministic conversion from simple rainfall CSV to:
  - SWMM `[TIMESERIES]` text lines
  - structured JSON manifest for audit/provenance
- Deterministic helper generation for SWMM `[RAINGAGES]` section.
- MCP wrapper for agentic use.

## Input CSV contract
`format_rainfall.py` expects a header row and at minimum:
- `timestamp`: date-time string, default format `%Y-%m-%d %H:%M`
- `rainfall_mm_per_hr`: rainfall intensity in mm/hr

You can override column names with CLI flags.

## Scripts
- `scripts/format_rainfall.py`
  - Reads rainfall CSV and writes:
    - `timeseries` text block for SWMM
    - machine-readable JSON summary
- `scripts/build_raingage_section.py`
  - Builds SWMM `[RAINGAGES]` snippet referencing a timeseries name.

## Outputs
- Timeseries text file (SWMM-ready body for `[TIMESERIES]`)
- JSON summary with:
  - source path + SHA256
  - timestamp range
  - row count
  - timeseries name
- Raingage snippet text file + JSON summary.

## MCP
MCP wrapper location:
- `scripts/mcp/server.js`

Exposed tools:
- `format_rainfall`
- `build_raingage_section`

## Example commands
```bash
python3 skills/swmm-climate/scripts/format_rainfall.py \
  --input skills/swmm-climate/examples/rainfall_event.csv \
  --out-json runs/swmm-climate/example_rainfall.json \
  --out-timeseries runs/swmm-climate/example_timeseries.txt \
  --series-name TS_EVENT
```

```bash
python3 skills/swmm-climate/scripts/build_raingage_section.py \
  --gage-id RG1 \
  --series-name TS_EVENT \
  --interval-min 5 \
  --out-text runs/swmm-climate/example_raingage.txt \
  --out-json runs/swmm-climate/example_raingage.json
```

## MVP limitations
- MVP focuses on rainfall intensity and raingage section helper only.
- No temperature/evaporation/wind climatology conversion in this pass.
