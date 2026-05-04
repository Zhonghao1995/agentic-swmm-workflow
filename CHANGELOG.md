# Changelog

All notable changes to Agentic SWMM Workflow are documented here.

## Unreleased

- Added GitHub Actions lightweight CI for syntax checks, uncertainty unit tests, and fuzzy uncertainty dry-run coverage.
- Added `CITATION.cff` so GitHub can expose repository citation metadata.
- Added root `requirements.txt` as the standard manual Python dependency entrypoint.
- Added this changelog for release-to-release visibility.

## v0.3.0 - Public agent memory and raw GeoPackage workflow

- Added the public OpenClaw/Hermes memory package under `openclaw/memory/`.
- Added ordered agent workflow memory for guiding users from input inventory through build, run, QA, audit, and readiness reporting.
- Added the TUFLOW SWMM Module 03 Raw GeoPackage-to-INP benchmark.
- Added a README figure showing generated subcatchments, conduits, junctions, and outfall from the raw GeoPackage benchmark.
- Reframed the README around five-minute one-command onboarding, OpenClaw/Hermes orchestration, verification-first modelling, and Obsidian-compatible audit memory.
- Added the Agentic SWMM logo and tightened README command examples with expandable sections.

## v0.2.0 - External multi-subcatchment benchmark

- Added the Tecnopolo prepared-input benchmark using an external 40-subcatchment SWMM model.
- Added benchmark evidence for direct `swmm5` execution, outfall and junction inspection, rainfall-runoff plotting, and audit-ready artifacts.
- Added README benchmark visualization for the Tecnopolo case.

## Earlier work

- Added one-command install scripts for macOS/Linux and Windows.
- Added modular SWMM skills for GIS, climate, parameters, network, builder, runner, plotting, calibration, uncertainty, audit, and end-to-end orchestration.
- Added fuzzy uncertainty propagation with triangular and trapezoidal membership functions, alpha-cut intervals, sampling, and dry-run support.
- Added Obsidian-compatible experiment audit workflow with provenance and comparison records.
- Added OpenClaw execution-path documentation and top-level `swmm-end-to-end` orchestration skill.
