---
name: swmm-climate
description: Deterministic rainfall/climate formatting for SWMM. Use when converting timestamped rainfall CSV files into SWMM-ready [TIMESERIES] lines and [RAINGAGES] helper snippets for swmm-builder.
---

# SWMM Climate (MVP rainfall layer)

Part of [Agentic SWMM](https://github.com/Zhonghao1995/agentic-swmm-workflow) — install the project first for the executable toolchain (aiswmm CLI, SWMM solver, MCP servers).

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

## SWMM `.dat` input contract
For SWMM-native rainfall `.dat` files (e.g. `<series> YYYY M D HH MM value`),
use `--input-dat <path>` and declare row units via `--dat-value-units`:
- `mm_per_hr`, `in_per_hr` (intensities)
- `mm_per_day`, `in_per_day` (24h volumes; divided by 24 to mm/hr)

In `.dat` mode the `--window-start` / `--window-end` filters expect `%Y-%m-%d`.
Use `--default-station-id` to override the series token taken from the .dat row.
`--input-dat` may be repeated to batch multiple .dat files but cannot be mixed
with `--input` / `--input-glob`.

Via the MCP tool, pass `inputDatPaths: [<path>]` and `datValueUnits:
"mm_per_day"` (or another supported unit) instead of `inputCsvPath`.

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
- `mcp/swmm-climate/server.js`

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

## Design storms

Use `design_storm.py` to synthesise a hyetograph from a return period and IDF coefficients
when no measured rainfall data exists. The output format matches `format_rainfall.py` so
`build_inp --rainfall-json` consumes it unchanged.

### Methods

| Method | When to use | Required inputs |
|--------|-------------|-----------------|
| `chicago` (Keifer-Chu) | IDF formula coefficients available | `--form`, coefficient flags, `--return-period`, `--duration` |
| `alternating_block` | Explicit IDF table (duration → intensity) | `--idf-csv` or `--idf-json`, `--duration` |

### IDF formula forms (chicago method)

**CN form** (`--form CN`): `q = 167·A1·(1+C·lgP)/(t+b)^n` [L/s/ha → converted to mm/hr]
Flags: `--a1`, `--C`, `--b`, `--n`

**Generic form** (`--form generic`): `i = a/(t+b)^c` [mm/hr]
Flags: `--a-coeff`, `--b`, `--c-exp`

### Example — 2-year Chicago hyetograph (CN form, 120 min, 5-min timestep)

```bash
python3 skills/swmm-climate/scripts/design_storm.py \
  --method chicago \
  --form CN \
  --a1 10.0 \
  --C 0.811 \
  --b 11.0 \
  --n 0.711 \
  --return-period 2 \
  --duration 120 \
  --dt 5 \
  --out-json runs/swmm-climate/storm_p2y.json \
  --out-timeseries runs/swmm-climate/storm_p2y.txt
```

Executed output:

```json
{
  "ok": true,
  "out_json": "/tmp/design_storm_test/storm_p2y.json",
  "out_timeseries": "/tmp/design_storm_test/storm_p2y.txt",
  "series_name": "TS_DESIGN_P2Y_120MIN",
  "series_names": [
    "TS_DESIGN_P2Y_120MIN"
  ],
  "rows": 24,
  "stations": 1,
  "interval_minutes": 5
}
```

### Example — alternating-block from an IDF table (inline JSON)

```bash
python3 skills/swmm-climate/scripts/design_storm.py \
  --method alternating_block \
  --idf-json '[{"duration_min":5,"intensity_mm_per_hr":60},{"duration_min":10,"intensity_mm_per_hr":45},{"duration_min":30,"intensity_mm_per_hr":28},{"duration_min":60,"intensity_mm_per_hr":18},{"duration_min":120,"intensity_mm_per_hr":11}]' \
  --duration 120 \
  --dt 5 \
  --return-period 2 \
  --out-json runs/swmm-climate/storm_ab_p2y.json \
  --out-timeseries runs/swmm-climate/storm_ab_p2y.txt
```

### MCP tool

`generate_design_storm` on the `swmm-climate` MCP server (third tool after `format_rainfall`
and `build_raingage_section`). Pass camelCase equivalents: `method`, `duration`, `outJson`,
`outTimeseries`, `form`, `returnPeriod`, `dt`, `r`, `a1`, `cCoeff`, `b`, `n`, `aCoeff`,
`cExp`, `idfCsv`, `idfJson`, `seriesName`.

## MVP limitations
- MVP focuses on rainfall intensity and raingage section helper only.
- No temperature/evaporation/wind climatology conversion in this pass.
- `swmm-builder` path in this repo still assembles a single raingage reference per build step.
