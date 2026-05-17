# Changelog

All notable changes to Agentic SWMM Workflow are documented here.

## Unreleased

## v0.6.3-alpha - Architecture deepening: intent_classifier + tool_handlers + RAG memory exposure (2026-05-16)

Pre-release on top of v0.6.2-alpha. Install with `pip install aiswmm==0.6.3a1` or `pip install --pre aiswmm`. Default `pip install aiswmm` continues to deliver v0.6.1.

This release does NOT introduce new user-facing CLI surface; it is an architectural enhancement release that consolidates and deepens what v0.6.2-alpha shipped. Architecture-audit findings #121 / #122 / #124 (memory portion only) / #127 / #128 are all closed by code here.

### Architectural changes

- **New deep module `agentic_swmm/agent/intent_classifier.py`** (#121). Consolidates keyword-driven intent resolution that was previously scattered across 6 files (`planner.py`, `tool_registry.py`, `single_shot.py`, `runtime_loop.py`, `continuation_classifier.py`, `intent_map.py`) into one auditable module. Exports `classify_intent(goal, *, workflow_state) -> IntentSignals` (dataclass) plus `is_negated(lowered, term)`. 31 new tests including bilingual EN/ZH symmetry, warm-intro gate, plot-continuation, and migration parity for the legacy entry points retained as re-export shims. Adding a new intent now touches one file.
- **New `agentic_swmm/agent/tool_handlers/` package** (#128 partial). Three skill families extracted from the 2163-LOC `tool_registry.py` monolith: `web.py` (`_web_fetch_url_tool`, `_web_search_tool`), `demo.py` (`_demo_acceptance_tool`), `swmm_memory.py` (`_recall_memory_tool`, `_recall_memory_search_tool`, `_recall_session_history_tool`, `_record_fact_tool` + token-budget helpers). The remaining 7 family slices are queued as follow-up PRs against `tool_registry.py`, which is now ~1900 LOC.
- **Dead-code purge in `agentic_swmm/agent/single_shot.py`** (#127). Module shrunk from 797 LOC to 144 LOC (-82 %).

### New agent capabilities

- **RAG memory retrieval is now agent-callable** (#124 Part A). New `retrieve_memory` ToolSpec wraps `skills/swmm-rag-memory/scripts/retrieve_memory.py` with `--query`, `--top-k`, `--retriever`, `--project` arguments. Read-only. New intent `memory-retrieval` matches `recall`, `前面`, `以前`, `类似` keywords.
- `agent/config/intent_map.json:mcp_enabled_skills` now lists all 11 registered MCP servers (previously 8). `integrations/mcp/README.md` updated from "eight" to "eleven".

### Portability completeness

- **`cases/` directory now ships with reference fixtures** (#122). `cases/tecnopolo/case_meta.yaml` and `cases/todcreek/case_meta.yaml` are committed templates with top-level `aliases` field so `_match_registered_case` picks up colloquial forms. Prior to this release, `cases/` was empty despite v0.6.2-alpha release notes claiming portability — that gap is now closed.
- **`agent/config/intent_map.json:swmm_request_keywords` no longer contains `tecnopolo`** (#122). The AST regression guard from #118 only walked Python `.py` files; the JSON config was a blind spot. The guard is now extended to scan `intent_map.json`.
- **`welcome.py` reads first registered case's `display_name`** instead of literal "tecnopolo" for the "Things to try" demo line; falls back to "Run a SWMM demo" when `cases/` is empty.
- **Bonus**: chart title in `skills/swmm-uncertainty/scripts/monte_carlo_propagate.py` made generic so the AST guard's pre-existing failure on that file is cleared.

### Documentation

- **README "preload path" no longer mentions stale `agent/memory/`** (#123). The mismatch was in `skills/swmm-end-to-end/SKILL.md` lines 10/38, fixed there.
- **README validation-snapshot anchors resolve** (#129). Two new sections added to `docs/validation-evidence.md`: `#information-loss-guided-subcatchment-partition` and `#prior-monte-carlo-uncertainty-smoke`. New CI-style test asserts every README `.md#anchor` link resolves.
- **Private-machine breadcrumbs removed from public docs** (#126). 14 `/Users/zhonghao` references removed across 5 files; regression test prevents future leaks.

### Hygiene

- **Plot script defaults are self-documenting** (#125). `skills/swmm-plot/scripts/plot_rain_runoff_si.py` no longer presents `TS_RAIN` / `O1` as silent defaults; they now read `<rainfall-series-name>` / `<outfall-or-junction>` and fail fast with a helpful error if a manual CLI hits them. The agent-driven flow always supplies explicit values, so the new error path is unreachable from the agent — but the regression test pins this invariant.

### Test count delta from v0.6.2-alpha

| PR | Tests added |
|---|---|
| #130 (#127) | 8 |
| #134 (#121) | 31 |
| #136 (#128) | 11 |
| #131 (#125) | 4 |
| #132 (#123 + #126 + #129) | 14 |
| #133 (#122) | 6 |
| #135 (#124 Part A only — Part B reverted in #137) | 6 |
| **Total** | **~80** |

### Known limitations carried forward

- **`tool_registry.py` split is partial** (3 of 10 family slices extracted in #128). Remaining 7 slices queued as future PRs.
- **`docs/framework-validation/saanich-b7-*/network.json`** retains `/Users/zhonghao` paths by design (frozen evidence). Same-directory README documents this.

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
