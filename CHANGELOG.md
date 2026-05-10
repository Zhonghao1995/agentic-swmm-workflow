# Changelog

All notable changes to Agentic SWMM Workflow are documented here.

## Unreleased

- No unreleased changes yet.

## v0.5.0 - CLI, Docker, Windows runtime, and structured workflow updates

- Added a stable `agentic-swmm` CLI layer for acceptance demos, prepared-input runs, audit, plotting, memory inspection, and environment checks.
- Added MCP runtime integration helpers, skill install helpers, generated MCP config support, and smoke testing for modular skill servers.
- Hardened Windows setup by using the active Python executable, improving local SWMM solver installation, and clarifying Windows install guidance.
- Improved Docker packaging defaults for the `v0.5.0` release, including updated image tags and the renamed `agentic-ai/memory/` trigger path.
- Added lightweight CI coverage for the CLI, audit workflow, Obsidian vault initialization, and SWMM runner peak parsing.
- Separated rainfall and flow plot panels for clearer runoff diagnostics and refreshed README figures for partitioning, uncertainty, and validation examples.
- Renamed the preload memory package from `openclaw/memory/` to `agentic-ai/memory/` to reflect Codex, OpenClaw, Hermes, and other Agentic AI runtimes.
- Updated `CITATION.cff` to match the latest `v0.5.0` repository release.
- Added an optional INP-derived raw adapter benchmark that fetches a fixed public `generate_swmm_inp` fixture, reconstructs raw-like inputs, and documents its evidence boundary.
- Refreshed validation and modeling-memory documentation after adding the adapter benchmark.

## v0.4.1 - README and memory-loading guidance polish

- Streamlined the README introduction to explain the memory-informed, verification-first workflow in plainer language.
- Moved detailed validation and benchmark evidence into `docs/validation-evidence.md` so the README stays focused.
- Clarified that `memory/modeling-memory/` is generated project memory, not startup instruction memory.
- Clarified the optional OpenClaw/Hermes loading path for `skills/swmm-modeling-memory/` and `memory/modeling-memory/` after multiple audited runs exist.

## v0.4.0 - Modeling memory and controlled skill refinement

- Added GitHub Actions lightweight CI for syntax checks, uncertainty unit tests, and fuzzy uncertainty dry-run coverage.
- Added `CITATION.cff` so GitHub can expose repository citation metadata.
- Added root `requirements.txt` as the standard manual Python dependency entrypoint.
- Added this changelog for release-to-release visibility.
- Added `skills/swmm-modeling-memory/`, a downstream modeling-memory skill that reads historical experiment audit artifacts without running SWMM or modifying existing skills.
- Added deterministic summarization of audited runs into `modeling_memory_index.json`, `modeling_memory_index.md`, `lessons_learned.md`, `skill_update_proposals.md`, and `benchmark_verification_plan.md`.
- Added controlled skill-refinement proposals for recurring assumptions, QA issues, missing evidence, failure patterns, and run-to-run differences.
- Added public example modeling-memory outputs under `memory/modeling-memory/`.
- Documented the memory loop as a core part of Agentic SWMM: each audited run can update project memory, while accepted skill changes still require human review and benchmark verification.
- Updated public Agentic AI memory to include the optional modeling-memory step after experiment audit.

## v0.3.0 - Public agent memory and raw GeoPackage workflow

- Added the public OpenClaw/Hermes memory package under `openclaw/memory/`, later renamed to `agentic-ai/memory/`.
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
