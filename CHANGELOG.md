# Changelog

All notable changes to Agentic SWMM Workflow are documented here.

## Unreleased

## v0.6.2-alpha - Runtime hygiene + paper-grade reproducibility hardening (2026-05-16)

Pre-release. Install with `pip install aiswmm==0.6.2a1` or `pip install --pre aiswmm`. Stable users on `pip install aiswmm` continue receiving v0.6.1.

### Bug fixes

- **Warm intro fires once per session, not on every greeting** (#108). The interactive-shell loop reset `turn = 0` after emitting the canned `WARM_INTRO_TEMPLATE`, causing the intro to re-fire on every subsequent open-shaped prompt. Source-level regression guard added in `tests/test_self_intro_on_open_prompt.py`.
- **First `plot_run` no longer hangs ~90s on matplotlib + swmmtoolbox cold start** (#109/#110). `mcp/swmm-plot/server.js` now fires a fire-and-forget preheat subprocess at server boot to materialize the matplotlib font cache + Python bytecode cache. Subsequent plot calls return in 5-15s on the user's machine instead of 89s.
- **Plot X-axis no longer renders as a solid black bar** (#112). `skills/swmm-plot/scripts/plot_rain_runoff_si.py` now uses `matplotlib.dates.AutoDateLocator(maxticks=12)` + `ConciseDateFormatter`. Tick count drops from 316 (30-day fixture) to 6 readable labels.
- **`swmm-end-to-end` and 5 sibling skills now discoverable via `select_skill`** (#113). `SkillRouter._build_buckets()` previously only knew the 8 skills with deterministic tool bindings, so pure-orchestration skills (only a `SKILL.md`) silently disappeared from `list_skills()`. Now seeded from the on-disk `discover_skills()` list.
- **Workflow router no longer hijacks compound intent like "run X demo and plot"** (#111). The keyword fallback in `_select_workflow_mode_tool` placed `wants_plot AND has_run_dir` before `wants_demo`, so "run Tod Creek demo and plot" was misclassified as `existing_run_plot` and would plot a different (Tecnopolo) run from prior global state. Fixed in two layers: (a) keyword-fallback priority — added `wants_run` signal and reordered branches; (b) new deep module `agentic_swmm/agent/intent_disambiguator.py` invokes a forced-enum LLM call only on detected plot+other-action conflicts (5s timeout, fail-soft to keyword fallback), preserving the deterministic SOP fast-path for unambiguous requests.

### Hardening

- **Doctor now warns on stale editable installs** (#113). `aiswmm doctor` emits a WARN row when the editable install resolves under `.claude/worktrees/`, pointing the user to re-run `pip install -e .` from the main checkout.
- **Doctor now warns on mcp.json drift** (#114). For each registered MCP server, doctor checks whether the launcher path is under the active `repo_root()`. WARN per drifted server with the remediation command.
- **New `aiswmm setup --refresh-mcp` flag** (#114). Regenerates only `~/.aiswmm/mcp.json` against the active editable install, leaves `config.toml` / `skills.json` / `memory.json` / `setup_state.json` untouched. Idempotent.

### Portability

- **Runtime contains zero hardcoded watershed names in routing/inference code** (#118). Prior to this release, `agentic_swmm/agent/runtime_loop.py:_case_slug`, `agentic_swmm/agent/continuation_classifier.py:_NEW_RUN_KEYWORDS`, and `skills/swmm-modeling-memory/scripts/summarize_memory.py:project_key` substring-matched `tecnopolo` / `todcreek` to decide case identity. New users applying aiswmm to a different watershed would silently get incorrect labeling. All three sites now consult `agentic_swmm.case.case_registry.list_cases()` (with optional `aliases` via `case_meta.extra`). AST-based regression guard in `tests/test_no_hardcoded_watershed_names.py` prevents future leaks.

## v0.5.0 - QGIS-backed entropy subcatchment preprocessing

- Added an auditable QGIS/GRASS-backed raw GIS preprocessing front end for entropy-guided SWMM subcatchment discretization.
- Added the generic `qgis_raw_to_entropy_partition` workflow so Tod Creek is a regression case instead of a hard-coded runner.
- Added QGIS layer normalization with CRS harmonization and boundary clipping for DEM, boundary, land-use, and soil layers.
- Added MCP-facing QGIS operations for `grass:r.watershed`, `native:reprojectlayer`, `native:clip`, `gdal:warpreproject`, and `gdal:cliprasterbymasklayer`.
- Added flow-connected WJE/NWJE/WFJS subcatchment partitioning with threshold-sensitivity figures and audit manifests.
- Added the cell-level entropy/fuzzy-similarity heterogeneity screening map as a QGIS preprocessing diagnostic.
- Renamed the preload memory package from `openclaw/memory/` to `agentic-ai/memory/` to reflect Codex, OpenClaw, Hermes, and other Agentic AI runtimes.
- Updated `CITATION.cff` to match the `v0.5.0` repository release.

## v0.4.2 - Adapter benchmark and validation refresh

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
- Updated public OpenClaw memory to include the optional modeling-memory step after experiment audit.

## v0.3.0 - Public agent memory and raw GeoPackage workflow

- Added the public Agentic AI memory package under `agentic-ai/memory/`.
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
