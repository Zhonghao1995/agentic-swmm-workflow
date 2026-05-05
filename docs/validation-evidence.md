# Validation Evidence

This document keeps the detailed benchmark and verification notes out of the README while preserving the evidence boundary for reviewers and collaborators.

## Benchmark paths

Agentic SWMM currently includes two external benchmark paths that test different parts of the workflow.

## Raw GeoPackage-to-INP benchmark

The TUFLOW SWMM Module 03 benchmark validates the structured raw GIS path. This is the stronger agentic workflow demonstration because it starts from public GeoPackage model layers and rebuilds the SWMM-ready structure before running QA and audit.

It converts public GeoPackage layers into SWMM-ready artifacts, including junctions, outfalls, conduits, subcatchments, raingages, multi-raingage rainfall inputs, `network.json`, `subcatchments.csv`, parameter JSON, a generated `model.inp`, SWMM outputs, QA summaries, and audit notes.

<p align="center">
  <img src="figs/tuflow_swmm_module03_raw_layers.png" alt="TUFLOW SWMM Module 03 raw GeoPackage layers converted into Agentic SWMM subcatchments, conduits, junctions, and outfall" width="520" />
</p>

Run:

```bash
python3 scripts/benchmarks/run_tuflow_swmm_module03_raw_path.py
```

See `../examples/tuflow-swmm-module03/README.md` for download instructions, expected artifacts, metrics, and the raw GeoPackage evidence boundary.

## Prepared-input SWMM benchmark

The Tecnopolo benchmark validates the prepared-input path using an external 40-subcatchment SWMM model derived from a public Zenodo dataset.

It checks that the workflow can execute an external SWMM model, compare workflow outputs against direct `swmm5` execution, inspect both an outfall and an internal junction, generate rainfall-runoff figures, and emit audit-ready artifacts.

<p align="center">
  <img src="figs/tecnopolo_199401_outfall_rain_runoff.png" alt="Tecnopolo January 1994 rainfall-runoff benchmark at OUT_0" width="900" />
</p>

Run:

```bash
python3 scripts/benchmarks/run_tecnopolo_199401.py
```

See `../examples/tecnopolo/README.md` for validation details, expected peak-flow checks, reproducibility notes, and the prepared-input evidence boundary.

## INP-derived raw adapter benchmark

The `generate_swmm_inp` adapter benchmark is an optional reproducibility check for the modular Agentic SWMM path. It fetches a fixed public upstream commit from `Jannik-Schilling/generate_swmm_inp`, reads the open `Test_5_2.inp` fixture, extracts raw-like GeoJSON, CSV, and JSON inputs, then rebuilds and runs the case through the repository's network, GIS, parameter, builder, runner, and QA modules.

Run:

```bash
python3 scripts/benchmarks/run_generate_swmm_inp_raw_path.py
```

Evidence boundary: this is not a greenfield watershed case from DEM, land-use, soil, and drainage-asset source files. Its source is an existing public SWMM `.inp`; the benchmark is useful for testing raw-like adapter handoff and modular reconstruction, not for claiming independent watershed delineation or hydrologic validation.

## Additional runnable paths

The repository also includes an acceptance pipeline for regression checks and a minimal Tod Creek real-data fallback path for environments where the Tod Creek example inputs are available.

```bash
python3 scripts/acceptance/run_acceptance.py --run-id latest
python3 scripts/real_cases/run_todcreek_minimal.py
```

## Experiment audit example

The audit layer consolidates artifacts, QA checks, and metric provenance into an Obsidian-compatible experiment note. This example catches a recorded peak-flow value that does not match the value re-parsed from the SWMM report source section.

<p align="center">
  <img src="figs/audit_comparison_example_readme.png" alt="Experiment audit comparison showing a peak-flow provenance mismatch" width="900" />
</p>

For agent-orchestrated runs, inspect the generated audit note before treating outputs as research evidence.

## Optional local verification

Run acceptance:

```bash
python3 scripts/acceptance/run_acceptance.py --run-id latest
```

Check the acceptance report:

```text
runs/acceptance/latest/acceptance_report.md
```

Audit the run:

```bash
python3 skills/swmm-experiment-audit/scripts/audit_run.py --run-dir runs/acceptance/latest
```

Add `--obsidian-dir <vault-folder>` to write a copy of the same note into Obsidian.

Make a rainfall-runoff plot from acceptance outputs:

```bash
mkdir -p runs/acceptance/latest/07_plot
python3 skills/swmm-plot/scripts/plot_rain_runoff_si.py \
  --inp runs/acceptance/latest/04_builder/model.inp \
  --out runs/acceptance/latest/05_runner/acceptance.out \
  --out-png runs/acceptance/latest/07_plot/fig_rain_runoff.png
```

## Evidence boundary

The current repository is strongest as a reproducible agentic workflow for prepared-input SWMM execution, structured raw GIS-to-INP benchmarks, QA, audit, plotting, calibration support, and uncertainty extension. It also provides a practical path for users to get running quickly and then grow toward richer case-specific modelling.

For fully greenfield watershed, subcatchment, and pipe-network generation directly from DEM, land use, soil, and drainage assets, the intended direction is to add case-specific delineation and parameterization evidence rather than overstate automatic generation before those examples are validated.
