---
name: swmm-water-quality
description: >
  Complete SWMM engine coverage: pollutant buildup/washoff simulation
  support and load reporting.  Validate water-quality config JSON,
  build INPs with WQ sections, and extract pollutant load summaries
  from completed runs.
---

# SWMM Water Quality Skill

## Purpose

Complete SWMM engine coverage for pollutant buildup/washoff simulation
and load reporting.  This skill provides:

1. `validate_wq_config.py` — validate a WQ config JSON before passing
   it to the builder.
2. `extract_wq_loads.py` — extract WQ load summaries from a SWMM RPT.

The water-quality sections (`[POLLUTANTS]`, `[LANDUSES]`, `[COVERAGES]`,
`[BUILDUP]`, `[WASHOFF]`, `[LOADINGS]`) are emitted by
`skills/swmm-builder/scripts/build_swmm_inp.py` via the
`--water-quality-json` flag (see also `build_inp` tool's
`water_quality_json` argument).

## Agent tool: `read_wq_loads`

Read pollutant load summaries from a completed run's .rpt file.  Returns
`wq_present=false` for non-WQ runs.

```
read_wq_loads(rpt_path="runs/my_run/model.rpt")
```

Returns a structured JSON with:

- `wq_present` (bool)
- `pollutants` — sorted list of pollutant names
- `runoff_quality_continuity` — mass-balance rows (metric + per-pollutant kg)
- `quality_routing_continuity` — routing mass-balance rows
- `subcatchment_washoff` — per-subcatchment loads (kg per pollutant)
- `link_loads` — per-link transport loads (kg per pollutant)
- `outfall_loads` — per-outfall flow stats + pollutant loads

## WQ config JSON schema

Top-level keys (all required when the key is present; empty arrays are valid):

```json
{
  "pollutants": [...],
  "landuses": [...],
  "coverages": [...],
  "buildup": [...],
  "washoff": [...],
  "loadings": []
}
```

### `pollutants` entries

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | string | required | No spaces |
| `units` | string | required | `MG/L`, `UG/L`, or `#/L` |
| `c_rain` | float | `0` | Concentration in precipitation |
| `c_gw` | float | `0` | Concentration in groundwater |
| `c_ii` | float | `0` | Concentration in RDII |
| `k_decay_per_day` | float | `0` | First-order decay (1/days) |
| `snow_only` | bool | `false` | Buildup during snow only |
| `co_pollutant` | string | `"*"` | Co-pollutant name or `"*"` |
| `co_fraction` | float | `0` | Co-pollutant fraction (0–1) |
| `init_conc` | float | `0` | Initial dry-weather concentration |

### `landuses` entries

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | string | required | |
| `sweep_interval` | float | `0` | Days between sweeping (0 = no sweeping) |
| `availability` | float | `0` | Fraction of buildup removed by sweeping (0–1) |
| `last_sweep` | float | `0` | Days since last sweep at start |

### `coverages` entries

| Field | Type | Notes |
|---|---|---|
| `subcatchment` | string | Must reference an existing subcatchment |
| `landuse` | string | Must reference a defined land use |
| `percent` | float (0–100) | Percent coverage; per-subcatchment sum must be ≤ 100 |

### `buildup` entries

| Field | Type | Notes |
|---|---|---|
| `landuse` | string | Must reference a defined land use |
| `pollutant` | string | Must reference a defined pollutant |
| `func_type` | string | `POW`, `EXP`, or `SAT` (`EXT` not supported in v1) |
| `c1` | float | Max buildup (kg/ha or count/ha when normalizer=AREA) |
| `c2` | float | Rate constant |
| `c3` | float | Third coefficient (unused for EXP/SAT) |
| `normalizer` | string | `AREA` or `CURBLENGTH` |

### `washoff` entries

| Field | Type | Notes |
|---|---|---|
| `landuse` | string | Must reference a defined land use |
| `pollutant` | string | Must reference a defined pollutant |
| `func_type` | string | `EXP`, `RC`, or `EMC` |
| `c1` | float | Coefficient 1 |
| `c2` | float | Coefficient 2 (0 for EMC) |
| `sweep_removal` | float (0–1) | Fraction removed by sweeping |
| `bmp_removal` | float (0–1) | Fraction removed |

### `loadings` entries (optional)

| Field | Type | Notes |
|---|---|---|
| `subcatchment` | string | Must reference an existing subcatchment |
| `pollutant` | string | Must reference a defined pollutant |
| `init_buildup` | float | Initial buildup mass |

## Scripts

- `scripts/validate_wq_config.py` — standalone CLI validator
- `scripts/extract_wq_loads.py` — RPT load extractor

## Executed examples

### Validate a WQ config JSON

```bash
# Write a minimal WQ config JSON:
cat > /tmp/wq_example.json << 'EOJSON'
{
  "pollutants": [{"name": "TSS", "units": "MG/L", "c_rain": 0, "c_gw": 0,
                  "c_ii": 0, "k_decay_per_day": 0, "snow_only": false,
                  "co_pollutant": "*", "co_fraction": 0, "init_conc": 0}],
  "landuses": [{"name": "Residential", "sweep_interval": 0, "availability": 0, "last_sweep": 0}],
  "coverages": [{"subcatchment": "S1", "landuse": "Residential", "percent": 100}],
  "buildup": [{"landuse": "Residential", "pollutant": "TSS", "func_type": "EXP",
               "c1": 15, "c2": 0.5, "c3": 0, "normalizer": "AREA"}],
  "washoff": [{"landuse": "Residential", "pollutant": "TSS", "func_type": "EMC",
               "c1": 50, "c2": 0, "sweep_removal": 0, "bmp_removal": 0}],
  "loadings": []
}
EOJSON

python3 skills/swmm-water-quality/scripts/validate_wq_config.py \
    --wq-json /tmp/wq_example.json
# Output: {"ok": true, "pollutant_count": 1, "landuse_count": 1, ...}
```

### Extract WQ load summaries from a completed run RPT

```bash
python3 skills/swmm-water-quality/scripts/extract_wq_loads.py \
    --rpt tests/fixtures/wq/wq_smoke.rpt
# Output: {"ok": true, "wq_present": true, "pollutants": ["TSS"],
#          "runoff_quality_continuity": [...], ...}
```

### Build an INP with water quality sections

```bash
python3 skills/swmm-builder/scripts/build_swmm_inp.py \
    --subcatchments-csv <subcatchments.csv> \
    --params-json <params.json> \
    --network-json <network.json> \
    --water-quality-json /tmp/wq_example.json \
    --out-inp /tmp/wq_model.inp \
    --out-manifest /tmp/wq_model_manifest.json
```

## Validation constraints

Enforced by both `validate_wq_config.py` and `build_swmm_inp.py`:

- Referential: every `[BUILDUP]`/`[WASHOFF]` landuse/pollutant must exist
- Referential: every `[COVERAGES]`/`[LOADINGS]` subcatchment must exist
- Enum: `units` ∈ {`MG/L`, `UG/L`, `#/L`}
- Enum: buildup `func_type` ∈ {`POW`, `EXP`, `SAT`} (EXT rejected with message)
- Enum: washoff `func_type` ∈ {`EXP`, `RC`, `EMC`}
- Range: coverage `percent` ∈ [0, 100]; per-subcatchment sum ≤ 100
- Range: `sweep_removal`, `bmp_removal`, `co_fraction` ∈ [0, 1]
