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

Optional extensions:
- `station_id` (or another column via `--station-column`) to carry multiple stations in one file.
- Batch mode by repeating `--input` and/or using `--input-glob`.
- Event window slicing via `--window-start` and `--window-end` (inclusive).

Accepted rainfall units (`--value-units`):
- `mm_per_hr` (aliases: `mm/hr`, `mm/h`)
- `in_per_hr` (aliases: `in/hr`, `in/h`)

Unit policy (`--unit-policy`):
- `strict`: only `mm_per_hr` accepted.
- `convert_to_mm_per_hr`: supported units are converted to `mm_per_hr`.

Temporal validation:
- duplicate timestamps are rejected per station/series.
- timestamp monotonicity is checked per station (`--timestamp-policy strict` default; optional `sort`).

## Scripts
- `scripts/format_rainfall.py`
  - Reads rainfall CSV and writes:
    - `timeseries` text block for SWMM
    - machine-readable JSON summary
- `scripts/build_raingage_section.py`
  - Builds SWMM `[RAINGAGES]` snippet referencing a timeseries name.
  - For rainfall JSON with multiple stations, use `--station-id` to choose one station’s series.

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
python3 skills/swmm-climate/scripts/format_rainfall.py \
  --input skills/swmm-climate/examples/rainfall_multi_station.csv \
  --station-column station_id \
  --series-name-template 'TS_EVENT_{station_safe}' \
  --out-json runs/swmm-climate/example_multi_station.json \
  --out-timeseries runs/swmm-climate/example_multi_station.txt
```

```bash
python3 skills/swmm-climate/scripts/format_rainfall.py \
  --input skills/swmm-climate/examples/rainfall_batch_rg1.csv \
  --input skills/swmm-climate/examples/rainfall_batch_rg2.csv \
  --window-start '2025-06-01 00:05' \
  --window-end '2025-06-01 00:15' \
  --series-name TS_BATCH \
  --out-json runs/swmm-climate/example_batch_windowed.json \
  --out-timeseries runs/swmm-climate/example_batch_windowed.txt
```

```bash
python3 skills/swmm-climate/scripts/build_raingage_section.py \
  --gage-id RG1 \
  --rainfall-json runs/swmm-climate/example_multi_station.json \
  --station-id RG1 \
  --interval-min 5 \
  --out-text runs/swmm-climate/example_raingage.txt \
  --out-json runs/swmm-climate/example_raingage.json
```

## MVP limitations
- MVP focuses on rainfall intensity and raingage section helper only.
- No temperature/evaporation/wind climatology conversion in this pass.
- `swmm-builder` path in this repo still assembles a single raingage reference per build step.
