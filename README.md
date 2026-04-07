# agentic-swmm-workflow

Reproducible **EPA SWMM** workflow with OpenClaw Skills and MCP tools for:
- preprocessing
- model execution
- plotting
- calibration / validation

## Modules
- `swmm-gis` → DEM-based outlet selection
- `swmm-runner` → reproducible `swmm5` runs + manifests
- `swmm-plot` → publication-style rainfall–runoff figures
- `swmm-calibration` → parameter scout, sensitivity, calibration, and validation scaffold

## Quick start
### Run SWMM
```bash
python3 skills/swmm-runner/scripts/swmm_runner.py run \
  --inp examples/todcreek/model_chicago5min.inp \
  --run-dir runs/demo \
  --node O1
```

### Plot rainfall–runoff
```bash
python3 skills/swmm-plot/scripts/plot_rain_runoff_si.py \
  --inp runs/demo/model.inp \
  --out runs/demo/model.out \
  --out-png runs/demo/fig_rain_runoff.png \
  --focus-day 1984-05-25 \
  --window-start 09:00 \
  --window-end 15:00 \
  --dt-min 5
```

### Calibration dry-run
```bash
python3 skills/swmm-calibration/scripts/swmm_calibrate.py calibrate \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --parameter-sets examples/calibration/parameter_sets.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/calibration \
  --swmm-node O1 \
  --objective nse \
  --summary-json runs/calibration/summary.json \
  --best-params-out runs/calibration/best_params.json \
  --dry-run
```

### Parameter scout
```bash
python3 skills/swmm-calibration/scripts/parameter_scout.py \
  --base-inp examples/todcreek/model_chicago5min.inp \
  --patch-map examples/calibration/patch_map.json \
  --base-params examples/calibration/base_params.json \
  --scan-spec examples/calibration/scan_spec.json \
  --observed examples/calibration/observed_flow.csv \
  --run-root runs/parameter-scout \
  --summary-json runs/parameter-scout/summary.json \
  --swmm-node O1
```

## MCP tools
`swmm-calibration-mcp` now exposes:
- `swmm_parameter_scout`
- `swmm_sensitivity_scan`
- `swmm_calibrate`
- `swmm_validate`

Start it with:
```bash
cd skills/swmm-calibration/scripts/mcp && npm install && npm start
```

## Repository map
- public repo structure: `docs/repo-map.md`
- calibration example inputs: `examples/calibration/README.md`

## Scope
Current calibration support is intentionally MVP:
- explicit candidate parameter sets
- one-parameter-at-a-time scout
- simple line-oriented INP patching
- transparent limitations rather than fake full automation

## Citation
Zhang, Z., & Valeo, C. (2026). *agentic-swmm-workflow* [Computer software]. GitHub. https://github.com/Zhonghao1995/agentic-swmm-workflow
