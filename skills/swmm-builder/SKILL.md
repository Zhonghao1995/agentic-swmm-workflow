---
name: swmm-builder
description: Assemble a runnable SWMM INP deterministically from subcatchment geometry/attributes, merged parameter JSON, network JSON, and climate references. Use when creating auditable INP + manifest artifacts for downstream swmm-runner/calibration.
---

# SWMM Builder (INP assembly layer)

## Contract
Build a runnable SWMM `.inp` using explicit file inputs:
- `subcatchments.csv` (shape/area/outlet/routing basics)
- merged params JSON from `swmm-params`
- network JSON from `swmm-network`
- rainfall/time-series references from `swmm-climate`
- optional options config JSON

The builder writes:
- final SWMM INP text (`--out-inp`)
- manifest JSON (`--out-manifest`) with source paths + SHA256 + key metadata
- strict validation diagnostics for critical sections (`[OPTIONS]`, `[RAINGAGES]`, `[TIMESERIES]`, `[SUBCATCHMENTS]`, `[SUBAREAS]`, `[INFILTRATION]`, and current network sections)

## Inputs
### Subcatchments CSV schema (required)
Required columns:
- `subcatchment_id`
- `outlet`
- `area_ha`
- `width_m`
- `slope_pct`

Optional columns:
- `rain_gage` (falls back to default gage from climate/config)
- `curb_length_m` (default `0`)
- `snow_pack` (default blank)

### Params JSON (required)
Expected to match `skills/swmm-params/scripts/merge_swmm_params.py` output:
- `sections.subcatchments` (`id`, `pct_imperv`)
- `sections.subareas` (`id`, runoff/subarea fields)
- `sections.infiltration` (`id`, Green-Ampt fields)
- Required fields are now validated strictly with type/range checks (for example `%Imperv` and routing percentages must be `0..100`).

### Network JSON (required)
Expected to match `skills/swmm-network` schema (`junctions`, `outfalls`, `conduits`, etc.).
Builder now validates required network fields used to emit `[JUNCTIONS]`, `[OUTFALLS]`, `[CONDUITS]`, `[XSECTIONS]`, `[COORDINATES]`, and `[VERTICES]`.

### Climate references (required in MVP)
Provide either:
- `--timeseries-text` directly, or
- `--rainfall-json` produced by `swmm-climate/format_rainfall.py` (must include `outputs.timeseries_text`)

For `[RAINGAGES]`, provide either:
- `--raingage-json` from `swmm-climate/build_raingage_section.py`, or
- rely on default deterministic gage generation from rainfall `series_name`.

`--raingage-json` supports both the original single-gage form and a multi-gage form:

```json
{
  "gages": [
    {
      "id": "RG1",
      "rain_format": "VOLUME",
      "interval_min": 5,
      "scf": 1.0,
      "source": {"kind": "TIMESERIES", "series_name": "TS_RG1"}
    },
    {
      "id": "RG2",
      "rain_format": "VOLUME",
      "interval_min": 5,
      "scf": 1.0,
      "source": {"kind": "TIMESERIES", "series_name": "TS_RG2"}
    }
  ]
}
```

The timeseries text must include rows for every referenced `series_name`, and each subcatchment `rain_gage` must reference one of the emitted gage IDs.

Validation behavior:
- Missing critical fields fail fast with explicit section-scoped errors.
- `[TIMESERIES]` rows are validated for series-name consistency and basic token/time/value correctness.
- Manifest includes `validation` plus `validation_diagnostics` metadata.

## Script
- `scripts/build_swmm_inp.py`
  - single entrypoint that reads all inputs and writes INP + manifest.

## MCP
MCP wrapper location:
- `scripts/mcp/server.js`

Exposed tools:
- `build_inp`

## Smoke example
```bash
python3 skills/swmm-builder/scripts/build_swmm_inp.py \
  --subcatchments-csv skills/swmm-builder/examples/subcatchments_input.csv \
  --params-json runs/swmm-params/example_builder_params.json \
  --network-json skills/swmm-network/examples/basic-network.json \
  --rainfall-json runs/swmm-climate/example_rainfall.json \
  --raingage-json runs/swmm-climate/example_raingage.json \
  --config-json skills/swmm-builder/examples/options_config.json \
  --out-inp runs/swmm-builder/example_model.inp \
  --out-manifest runs/swmm-builder/example_manifest.json
```

## MVP limitations
- Generates core hydrology/hydraulics sections only.
- No automatic polygon export, LID controls, snowpack, RTC rules, or pollutant quality in this pass.
- Assumes one raingage source for all subcatchments unless `rain_gage` is explicitly set per row.
