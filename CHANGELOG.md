# Changelog

All notable changes to Agentic SWMM Workflow are documented here.

## v0.7.3 - One-command install on Windows, latest-release by default (2026-06-13)

Reworks the one-line installers so a single command provisions the full toolchain on a fresh machine, and makes both platforms default to the latest published release.

### Fixed — Windows one-line install

- The Windows one-liner (`irm https://aiswmm.com/install.ps1 | iex`) failed on fresh machines. The bootstrap now clones into `%LOCALAPPDATA%` instead of the current directory (an elevated shell defaults to the write-protected `C:\Windows\System32`) and sets a process-scope `ExecutionPolicy Bypass` before running the cloned installer (the default `Restricted` policy blocked it).
- The installer rejects the Microsoft Store `python.exe` / `python3.exe` App-execution-alias stubs and auto-installs Python 3.12 (required) and Node.js LTS (best-effort; MCP is skipped if Node is unavailable) via `winget`, refreshing PATH in the running session.
- The venv's `Scripts` directory is added to the user PATH, so `aiswmm` resolves after install instead of reporting "not recognized".

### Changed — installer entrypoints (both platforms)

- `web/install.ps1` and `web/install.sh` now default to the **latest published release** (resolved via the GitHub API); set `AISWMM_INSTALL_REF` to pin a tag (e.g. `v0.7.2`) or `main`. The bootstrap honors that ref and clones the matching tag.
- The upfront OpenAI-only model prompt is gone; the AI provider and model are chosen after install via the CLI (`aiswmm login` for OpenAI, `aiswmm login --anthropic` for Claude).
- A CI job keeps `aiswmm.com/install.{ps1,sh}` in lockstep with `web/install.*`, so the website entrypoints never drift from this repo.

## v0.7.2 - Agent-reachable calibration & sensitivity, three new skills, design storms, memory observability (2026-06-11)

Released alongside our paper in *AI for Engineering* ([doi:10.3390/aieng1010005](https://doi.org/10.3390/aieng1010005)). 70 commits since v0.7.1: the planner's typed-tool surface grows from 38 to **55 tools**, the skill library from 15 to **18 skills**, and the test suite from 2,318 to **2,799 tests** — full suite green, SWMM execution byte-identical.

### Added — calibration & sensitivity analysis become first-class typed tools (v0.7.2)

- **6 calibration tools** (`swmm_sensitivity_scan`, `swmm_calibrate`, `swmm_calibrate_search`, `swmm_calibrate_sceua`, `swmm_calibrate_dream_zs`, `swmm_validate`) and **5 uncertainty tools** (`swmm_sensitivity_oat`, `swmm_sensitivity_morris`, `swmm_sensitivity_sobol`, `swmm_rainfall_ensemble`, `swmm_uncertainty_source_decomposition`) registered as typed ToolSpecs — the planner selects SCE-UA / DREAM-ZS calibration and Morris / Sobol' screening by name instead of via the generic `call_mcp_tool` escape hatch. Intent hints corrected to point at the real tools; a new parity test locks every `preferred_tools` entry to a registered tool name. `swmm-gis` and `swmm-params` stay `call_mcp_tool`-only by design (QGIS-desktop dependencies / pipeline glue) — recorded in CONTEXT.md.
- **Smaller reachability fixes**: `build_raingage_section` registered; `audit_run` gains `compare_to`; `summarize_memory` gains `obsidian_dir`; `format_rainfall` exposes its full input surface (glob / .dat / multi-station); `plot_run` forwards `focus_day` / `window_start` / `window_end`; `synth_swmm_from_bbox` exposes `config_overrides`; `retrieve_memory` bound to its skill.

### Added — three new skills (v0.7.2)

- **`swmm-water-quality`** — completes SWMM engine coverage for pollutant buildup/washoff: the builder emits `[POLLUTANTS]`/`[LANDUSES]`/`[COVERAGES]`/`[BUILDUP]`/`[WASHOFF]`/`[LOADINGS]` from a `--water-quality-json` config (engine-smoke-verified on SWMM 5.2.4 at 0.000% quality continuity error), the canonical rpt parser gains the four water-quality summary sections, the audit note reports pollutant loads, and `read_wq_loads` exposes them to the planner.
- **`swmm-design-review`** — deterministic rule-checklist engine over a completed run (INP + RPT + manifest): YAML rulebooks (rules are data, not code), `pass / fail / warn / needs-data` per rule with evidence pointers, `aiswmm review` CLI verb and `review_run` typed tool. Ships a GB 50014-class template rulebook in which **every threshold is marked `verify: true`** — the tool reports findings against a user-confirmed rulebook; it never certifies compliance.
- **`swmm-report`** — assembles a run's audit artifacts, metrics tables, figures, and the provenance sha256 table into an engineering-formatted Word deliverable (numbered sections, table captions with explanatory narratives, page numbers): `aiswmm report` CLI verb, `generate_report` typed tool, nine-section user-overridable YAML template, deterministic content (timestamps from provenance, never the clock). Installs via the `aiswmm[report]` extra (python-docx).

### Added — design storms (v0.7.2)

- **`generate_design_storm`** — synthesise a Chicago (Keifer-Chu; CN 167-form or generic IDF form) or alternating-block hyetograph from a return period + IDF coefficients, writing the same `--out-json` / `--out-timeseries` contract `format_rainfall` produces, so `build_inp` consumes it unchanged. Storm total equals the IDF depth for the design duration exactly. The legacy explicit-depth shape library (uniform/triangular/huff/scs) remains available as `generate_storm_shape`.

### Added — memory observability & application provenance (v0.7.2)

- **`memories_applied`** — runs record which memory entries programmatically shaped their inputs, in `manifest.json` and `experiment_provenance.json`: a deliverable is now traceable to the memory entries behind it.
- **Application outcome log** (`memory/modeling-memory/memory_outcome_events.jsonl`) — the post-audit hook appends one outcome event per applied memory (within band / below band / run failed / contradicted / reconfirmed); the derived per-entry **health score** is inspectable via `aiswmm memory health <id>`.
- **Health-aware recall** — entries rank by health × relevance; *watch*-tier entries are recalled with an evidence-bearing caution; *archived*-tier entries are excluded by default, with explicit `aiswmm memory archive` / `restore` verbs (live stores remain human-gated; read-time filtering never mutates them).
- **Memory context budget** — the session-start memory block is capped (default 4,000 chars, `memory.context_budget_chars`) with ranked packing and a trace event for exclusions; recall ranking gains an optional recency weighting (`memory.recall_half_life_days`, off by default).
- **New-case onboarding re-wired** — starting a watershed the system has not seen offers transferred starter parameters from similar past cases (planner-side offer hook + `apply_onboarding` typed tool), restoring the surface orphaned by the dispatch refactor.

### Fixed (v0.7.2)

- **Agent default `run_dir` collision** — the `synth_swmm_from_bbox` default directory was non-timestamped, so re-running a project name silently overwrote the previous run's outputs; now `runs/agent/<safe>-<unix-ts>` with an exists-bump. Explicit `run_dir` passthrough (and the `00_raw/` snapshot-reuse workflow) unchanged.
- **`Outfall Loading Summary` dropped rows in water-quality runs** — the parser's fixed column-count match excluded rows carrying pollutant columns; now a minimum-width match, byte-identical for non-WQ runs.
- **`.rpt` parsing consolidated** — the canonical section parser (`rpt_summary.py`) absorbed the largest duplicate, and 23 parity tests pin all five historical parser implementations to identical numbers.
- **SKILL.md drift sweep** — eight skills' runtime-read docs corrected against code (runnable smoke examples, real CLI flags, current tool lists); dispatch-refactor dead code removed.

### Changed — two API-key LLM providers (OpenAI default + Anthropic opt-in)

The planner is driven by one of two **API-key** backends, both using standard
function-calling over pure-stdlib `urllib` (no SDK, no subprocess): `openai`
(the default, OpenAI Responses API) and `anthropic` (opt-in, native Anthropic
Messages API). This supersedes the short-lived, never-released subscription
design — routing through an agent SDK made Claude emit its own built-in tools
instead of aiswmm's registered tools, so raw function-calling was chosen for
robustness (accepting per-token cost). The provider/auth layer stays
factory-only, so adding a backend is a `factory.SUPPORTED_PROVIDERS` +
`make_provider` change.

#### Added (provider layer)

- **Native Anthropic provider** (`agentic_swmm/providers/anthropic_api.py`) — hits `https://api.anthropic.com/v1/messages` with `anthropic-version: 2023-06-01`, translating aiswmm's OpenAI-shaped tool descriptors (`parameters` → `input_schema`) and Responses-style `input_items` (→ `messages` with `tool_use` / `tool_result` blocks). Reads `ANTHROPIC_API_KEY`; honours `AISWMM_ANTHROPIC_MOCK_RESPONSE` / `AISWMM_ANTHROPIC_MOCK_TOOL_CALLS` for offline tests. No new dependency.
- **`aiswmm login --anthropic`** — stores `ANTHROPIC_API_KEY` in `~/.aiswmm/env` (mode 0600), pins `provider.default = anthropic` + `anthropic.model = claude-sonnet-4-6`. A bare `aiswmm login` targets the current default provider's key.

#### Changed (provider layer)

- **Default provider is `openai`** (`DEFAULT_PROVIDER = "openai"`, model `gpt-5.5`). `anthropic` is the opt-in second backend (`DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"`); both providers require a model, supplied by per-provider config defaults.
- **`SUPPORTED_PROVIDERS = ("openai", "anthropic")`** — the factory drops the subscription branch; an unknown provider (including the retired `claude_sdk`) raises `ValueError`.
- **`aiswmm login` manages API keys** via a `provider → handler` registry; `--status` reports the default provider and which keys are present (no secrets).
- Doctor / setup / welcome surface a two-API-key view (OpenAI default + Anthropic opt-in); the welcome tip and doctor key-row key on the default provider's key.

#### Removed (provider layer)

- The `claude_sdk` provider, the `claude-agent-sdk` core dependency, the `[claude]` extra, and all subscription / macOS-Keychain detection logic.

## v0.7.1 - SWMManywhere natural-language integration + runtime hardening (2026-05-28)

A single natural-language sentence referring only to a WGS84 bounding box now drives an end-to-end SWMM workflow: synthesise the drainage network from public OSM + DEM data, run SWMM, write a deterministic audit dossier, and render a spatial network map — all via the standard `runs/<date>/<id>/` layout. Synthesis comes from SWMManywhere (Imperial College London, BSD-3-Clause); aiswmm is the agent-side adapter. SWMM execution is byte-identical to v0.7.0 — Tecnopolo `model.out` SHA256 unchanged.

### Added — three new LLM-facing typed tools (v0.7.1)

- **`map_run`** — render the spatial network layout (subcatchments + conduits + outfalls) of a SWMM model as a PNG. Sibling of `plot_run` at the LLM surface (plot_run = hydrograph; map_run = network map). In-process wrapper around the `aiswmm map` CLI verb so the agent can request a network figure in one step. 14 unit tests + family-mapping drift entry.
- **`plot_run.link`** — new `link` parameter renders a conduit Flow_rate hydrograph when set. Mutually exclusive with `node` (which still renders the node-attribute hydrograph). Plumbed through three layers: ToolSpec schema, `_plot_run_args` mapper (forwards link, suppresses node, picks a sensible default `out_png` filename by link id), and the swmm-plot MCP server's zod Args + CallToolRequestSchema handler (emits `--link` or `--node` to the underlying script, never both). 9 unit tests.
- **`read_rpt_summary`** — parses SWMM .rpt summary sections (`Link Flow Summary` / `Outfall Loading Summary` / `Node Inflow Summary`) into structured JSON rows sorted by the per-section peak/max column. Replaces the `read_file`-with-4000-char-cap workaround on 300+ KB rpts. The ToolSpec description explicitly steers the LLM to call the tool once per section needed ("CALL THIS TOOL ONCE PER SECTION YOU NEED — the tool is stateless") and to use it instead of `read_file` or `search_files` for rpt data. Verified on the 2026-05-28 Tecnopolo run: the LLM called `read_rpt_summary` four times in a single run with different `section` values. 25 unit tests.

### Fixed (v0.7.1)

- **MCP transport `spawn ENOEXEC`.** A zero-byte `.venv/bin/python` stub (left by a half-finished venv or a test-fixture leak) caused the Node MCP launcher to assign `env.PYTHON` to an unusable executable. The downstream `spawn(PY, ...)` call returned `ENOEXEC` because the kernel cannot recognise an empty file as an executable. Fixed in two layers: `agentic_swmm/utils/subprocess_runner.py:runtime_env()` now pins `PYTHON=sys.executable` so the launcher inherits the correct interpreter, and `scripts/run_mcp_server.mjs` adds `isUsableInterpreter()` that rejects empty / non-executable candidates before assignment. Regression test plants a zero-byte stub and asserts the launcher does not select it.
- **`final_report.md` "What you got" listed `SKILL.md` paths instead of real artifacts.** `_what_you_got` was reading only `result["path"]`, and only `_read_skill_tool` put a path there. Production handlers nested artifact paths under `result["results"]` / `result["excerpt"]` (MCP JSON) / `result["args"]` / `result["summary"]`. New recursive `_mine_paths()` harvests artifact paths from anywhere in the result payload, with planner-internal-fragment filtering and an introspection-tool skip set (`read_skill`, `read_file`, `list_*`, `select_skill`, `search_files`, `capabilities`). Reports now list the produced `synth.inp`, `model.rpt`, `network_map.png`, audit JSONs in their correct sections. 13 unit tests.
- **`--max-steps` default 16 → 40** in both `aiswmm agent` and `aiswmm chat`. The 16-step ceiling cut the planner off mid-workflow because `gpt-5.5` typically spends ~15 steps on introspection (`list_skills`, `read_skill ×N`, `list_mcp_tools`, `select_skill`) before the first real op. The new default leaves ~25 steps of headroom. Pinned by 4 tests.

### Changed (v0.7.1)

- **`skills/swmm-anywhere/SKILL.md` install + attribution language humanised.** Same substance (BSD-3-Clause, Imperial College London, GitHub URL, citation request) reframed as one researcher crediting another rather than compliance copy. Upstream-attribution paragraph merged into the install section so it cannot be missed.
- **`.gitignore`** now covers `*.egg-info/`, caches (`.cache/`, `cache/`, `.pytest_cache/`), coverage output (`.coverage`, `htmlcov/`), Docker-mounted run outputs (`docker-runs/`), memory runtime side-files (`command_trace.json`, `project_overrides.yaml`, `.last_refresh_error.json`), spike research artifacts (`scripts/spike_swmmanywhere/`), and local-only experimental data dirs (`data/Todcreek/of1/`, `examples/hand1/`). `package-lock.json` now tracked for reproducible MCP-server npm installs.

### Evidence (v0.7.1)

- **Byte-identical reproducibility re-verified.** Tecnopolo `model.out` SHA256 = `85c5514a81ea745ebb0c1c3e2aebb0c2cc0d5a6aa3ef00a0fa6c8f7b760be38c` on v0.7.1, identical to the 2026-05-15 canonical lock-in across macOS native vs Docker stacks. Downstream tests that pin this SHA can upgrade v0.7.0 → v0.7.1 without re-baselining. See `docs/byte-identical-reproducibility.md`.
- **Minimum NL prompt length for the full Tecnopolo chain is 11 words.** `examples/tecnopolo/tecnopolo_r1_199401.inp。run it and audit it and plot the result` reaches synthesise-free SWMM execution + audit + plot end-to-end on the standard prepared INP. See `docs/byte-identical-reproducibility.md` for the recipe.
- **Cross-session memory layer autonomously activated on a real run.** The LLM planner, with no memory-related keyword in the user prompt, issued `recall_session_history(case_name="tecnopolo")` and recovered two prior Tecnopolo sessions from 12 days earlier — the first user-observable activation of the memory layer on a real workflow. See `docs/v0.7.1-cross-session-memory-evidence.md`.
- **Natural-language SWMManywhere chain end-to-end on two independent regions.** Greenwich Peninsula (1×1 km) canonical case study figures + NYC Midtown (1×1 km) cross-geography verification. See `docs/v0.7.1-swmmanywhere-nl-driven-evidence.md`.

### Out of scope — next milestone

v0.7.1 ships the **agent-side plumbing** for SWMManywhere and the cross-session memory layer. The modelling-science quality bars are explicitly out of scope and tracked as next-milestone work: calibration of the synthesised network against observed flows, systematic continuity-error characterisation across bbox sizes, a memory-aware calibration loop, negative-precedent handling in memory recall, and time-decay weighting for stale precedents.

### Attribution

Network synthesis in v0.7.1 is the work of [**SWMManywhere**](https://github.com/ImperialCollegeLondon/SWMManywhere) by Imperial College London (BSD-3-Clause licensed). Please cite SWMManywhere in any publication that uses or extends the SWMManywhere workflows shown in this release.

---

## v0.7.0a3 — pre-v0.7.1 dispatch refactor + SWMManywhere skill landing (developer-only marker)

The work below was developed on the `feat/swmmanywhere` branch and ships to users as part of v0.7.1. It is preserved here as a separate developer-facing section because it landed as a coherent design effort over the two weeks before v0.7.1 cut.

### Changed — LLM-driven dispatch refactor

- **`select_workflow_mode` tool removed from the registry.** v0.7.0 placed a forced `select_workflow_mode` first-hop in front of every SWMM-shaped goal: the tool's seven-value enum (`calibration` / `uncertainty` / `prepared_inp_cli` / `full_modular_build` / `existing_run_plot` / `audit_only_or_comparison` / `prepared_demo`) was a GPT-4-era defensive guardrail that hid the concrete SWMM tools from the LLM behind one big "pick a mode" tool. Frontier 2026-era LLMs pick the right function from a flat tools list with high accuracy when each description is well-written; the gate was throwing that capability away and forcing keyword re-classification on top of the LLM's own classifier.
- **`agentic_swmm/agent/workflow_modes/` directory deleted (12 files, ~1100 LOC).** The per-mode adapter registry that `_dispatch_workflow_mode` routed into is gone. Each adapter was a thin wrapper around a sequence of constrained tool calls; the LLM can now decide the same sequence by reading each tool's description / SKILL.md.
- **`agentic_swmm/agent/intent_disambiguator.py` deleted.** Its trigger (`wants_plot AND wants_run/demo/calibration/uncertainty`) was a GPT-4-era hedge against keyword-classifier overmatch. With the mode gate gone, the disambiguator has nothing to disambiguate.
- **`agentic_swmm/agent/tool_handlers/workflow_mode.py` deleted.** The handler for the deleted gate.
- **New typed-tool handler `agentic_swmm/agent/tool_handlers/swmm_anywhere.py`** exposes the `synth_swmm_from_bbox(bbox, run_dir?, project_name?, refresh_raw?, upstream_defaults?, rain_file?)` tool that wraps `swmmanywhere_runner.run_synth_from_bbox`. The legacy mode enum had no `synth-from-bbox` value, so a "use SWMManywhere on this bbox" prompt always fell through to the wrong mode — that real failure case is what surfaced the refactor.
- **Planner simplified.** `OpenAIPlanner.run` no longer forces a `select_workflow_mode` step or routes through `_dispatch_workflow_mode` / `_maybe_disambiguate` / `_classify_plot_continuation`. The LLM sees the full `AgentToolRegistry.schemas()` on every turn and picks tools by name. The pre-LLM `_consult_workflow_skills` (context priming) and `_consult_memory_informed_policy` (HITL escalation surface) hooks are unchanged.
- **System prompt rewritten.** "always call `select_workflow_mode` first" replaced by "read the SKILL.md description for each candidate before invoking a SWMM tool; the description plus the typed schema is the contract you commit to". Agent-internal tool list drops the workflow-mode-selection entry.
- **Capabilities surface updated.** `aiswmm capabilities` no longer lists `select_workflow_mode` under the "Build" group; the new typed `synth_swmm_from_bbox` entry point takes its place.
- **Upstream alignment.** Tool surface now mirrors the OpenAI function-calling and Anthropic tool-use APIs: each tool has a name + description + typed parameter schema, and the LLM picks tools from a flat registry. No `select_workflow_mode`-shaped routing layer between the LLM and the real tools.
- **Migration impact.** Interactive shell behaviour is unchanged from a user's perspective — they never typed `select_workflow_mode` themselves; only the planner did. Anyone with external code that imported `agentic_swmm.agent.workflow_modes`, `agentic_swmm.agent.intent_disambiguator`, or `agentic_swmm.agent.tool_handlers.workflow_mode` will need to update. The `workflow_mode` *string* survives as an optional tag on `audit_run` payloads for provenance bookkeeping; it is no longer a routing surface.
- **PRD + ADR.** Decision record lives at `.claude/prds/PRD_llm_driven_dispatch.md`; `CONTEXT.md` gains a "Dispatch architecture: LLM-driven over hardcoded mode enum" section after the existing real-data / synth-data discussion.
- **Tests:** 13 dispatch-layer test files deleted (their behavioural contract is gone), 5 test files modified to drop dispatch-layer assertions, and a new `tests/test_llm_driven_dispatch.py` (5 integration tests) pins the post-refactor contract — bbox prompt + scripted LLM picking `synth_swmm_from_bbox` reaches the executor with no gate, INP-path prompt picks `run_swmm_inp` directly, and `select_workflow_mode` never appears in any plan. Suite: 2140 passing post-refactor.

### Added — `swmm-anywhere` skill (PRD swmmanywhere_integration)

- **New skill `skills/swmm-anywhere/`** synthesises a plausible SWMM drainage network from a bounding box when no real pipe-network data exists. Wraps [ImperialCollegeLondon/SWMManywhere](https://github.com/ImperialCollegeLondon/SWMManywhere) (BSD-3-Clause) — © Imperial College London. End-to-end chain (bbox → OSM/DEM download → 24-step graphfcn pipeline → synth INP → aiswmm `swmm5` → audit + plot) verified at ~38 s on a 1×1 km London Greenwich bbox; peak flow parses cleanly through the standard audit pipeline.
- **New optional dependency extra** `pip install aiswmm[anywhere]` pulls in the ~27 geo dependencies (geopandas, osmnx, rasterio, pyflwdir, pywbt, …) only for users who opt in. Default `pip install aiswmm` footprint is unchanged.
- **New deep modules** `agentic_swmm/integrations/raw_snapshot.py` (reusable OSM/DEM hash + cache + verify under `runs/<id>/00_raw/`) and `agentic_swmm/integrations/swmmanywhere_runner.py` (Python wrapper with structured `SynthRunResult` / `SynthRunError`). The wrapper handles three macOS arm64 / SWMManywhere v0.2.2 gotchas inline: pyswmm SIGKILL on import (stubbed before SWMManywhere loads), `base_dir` str→Path coercion, and SWMM 5.2 `ERROR 205` when `[RAINGAGES] FILE` path contains spaces (external files copied next to the INP and the reference rewritten as a bare filename).
- **Default `outfall_derivation` parameters tuned** in spike 04 A/B testing (`method="withtopo"` + `river_buffer_distance=300` + `outfall_length=200`) — reduces outfall count by ~34 % vs SWMManywhere defaults on the spike test bbox.
- **New CLI script** `skills/swmm-anywhere/scripts/synth_from_bbox.py` is a thin argparse wrapper around `run_synth_from_bbox` that drops outputs into the standard `runs/<date>/<id>/` audit-pipeline layout.
- **Planner routing defended in 4 layers** so the LLM planner cannot pick `swmm-anywhere` when the user has real pipe data: (a) exclusive wording in `swmm-anywhere`'s SKILL.md (`"ONLY when no real pipe-network data exists"`), (b) reverse pointers in `swmm-gis` / `swmm-network` SKILL.md, (c) a `synth-from-bbox` intent block in `agent/config/intent_map.json` with an `exclusive_when` rule, (d) a routing rule in `swmm-end-to-end` SKILL.md choosing between real-data and synth-data entry skills based on whether the user attached `.shp`/`.csv`/`network.json`/`.inp`.
- **`CONTEXT.md` gains a "Real-data path vs Synth-data path" section** making the new orthogonal axis explicit for any agent or contributor reading the doc.
- **`aiswmm doctor`** now reports a `swmm-anywhere extra` row (`installed` / `not installed` with the install hint) so users can see at a glance whether the synth path is callable.
- 21 new unit tests (9 `raw_snapshot`, 10 `swmmanywhere_runner`, 2 CLI smoke); D1 verification spike scripts live under `scripts/spike_swmmanywhere/` (gitignored isolated venv + reproducible e2e driver).

## v0.7.0 - Modeling memory, agent runtime, install UX (2026-05-27)

First stable point release on the 0.7.x line. Promotes the v0.7.0a1 / a2 prereleases to stable, folds in the v0.7.0a2 architecture refactor wave that never had its own changelog, and applies the onboarding hotfixes uncovered by the v0.7.0a* dogfood. Default `pip install aiswmm` now resolves to v0.7.0; v0.6.4 remains available on every pinned channel (PyPI, Git tag, Docker image) for paper-aligned reproducibility runs.

### Added

- **Modeling-memory substrate.** An on-disk memory layer under `memory/modeling-memory/`: `parametric_memory` (run-level parameters and QA metrics), `calibration_memory` (accepted calibrations and goodness-of-fit), `reference_benchmarks` (library defaults) with a per-project `project_overrides.yaml` overlay, a citation library, and `negative_lessons` (known-bad parameter regions). Includes watershed-similarity matching, SQLite indexing for large stores, lifecycle-managed `lessons_learned.md` with three-tier decay (`active` → `dormant` → `retired` → archived), and an LLM-assisted reflection workflow (`aiswmm memory reflect --apply`) for human-in-the-loop curation. Scaffold the directory with `aiswmm bootstrap memory`.
- **Memory-informed runtime.** The planner can read modeling memory to disambiguate ambiguous requests, adapt QA thresholds to project history, and carry parameter priors across watersheds, with a transparency log of which memory entries were used. Opt out per run with `--ignore-memory`.
- **Claude Agent SDK provider (optional).** A second LLM backend that routes the planner through a Claude Pro/Max subscription via the local `claude` CLI. Install with the optional extra `pip install aiswmm[claude]`. The default OpenAI provider is unchanged and pulls none of this. The provider is gated behind the `AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS` env flag while it stabilises.
- **New CLI verbs.** `aiswmm compare` (per-node / per-subcatchment run diffs), `aiswmm storm` (Chicago / Huff / SCS design hyetographs), `aiswmm trace` (inspect the agent trace), `aiswmm uncertainty plan`, and `aiswmm bootstrap memory`.
- **Agent runtime error boundary.** New `@on_exception_return_default` decorator gives tool handlers a uniform soft-fail contract — exceptions become structured error objects in the agent trace instead of crashing the turn.
- **`sessions.sqlite` integrity check + repair path.** The runtime detects truncated or corrupted session databases at startup and offers a non-destructive repair (rebuilds FTS index, recovers recoverable messages) before falling back to a clean reset.
- **`CONTEXT.md`.** A repository-root document that captures the domain vocabulary (Session / Run / Case / Provider / Memory / Skill / MCP) and architectural decisions, intended as the canonical onboarding artifact for new contributors and AI agents.

### Changed

- **CLI/UX overhaul.** A unified flag convention across every verb (`--inp` / `--json` / `--quiet` / `--example`), grouped `--help` output, differentiated `error: / cause: / hint:` messages, and an honesty layer that detects SWMM `ERROR` output and stub modes instead of reporting false success. SWMM error text is now routed to stderr.
- **Calibration workflow closure.** Batch-aware planning, run-progress reporting, and resource estimation.
- **SWMM solver-version mismatch refused** rather than run silently.
- **Tool registry deep-modularised.** The 2163-line `tool_registry.py` monolith was split into focused handler packages — `tool_handlers/{web, demo, swmm_memory, swmm_runner, swmm_plot, swmm_builder, swmm_network, swmm_climate, swmm_audit, workflow_mode, introspection, runtime_ops, gap_fill}` — with shared helpers in `tool_handlers/_shared.py`. Adding a new tool family now touches one file instead of grepping a wall.
- **Intent classifier consolidated.** Keyword-driven intent resolution previously scattered across six modules (`planner.py`, `tool_registry.py`, `single_shot.py`, `runtime_loop.py`, `continuation_classifier.py`, `intent_map.py`) is now centralised in `agent/intent_classifier.py` with a single `classify_intent(goal, *, workflow_state) -> IntentSignals` entrypoint. Bilingual EN/ZH parity, warm-intro gating, and plot-continuation logic are preserved.
- **Runtime-loop bootstrap phases extracted.** The interactive shell's startup sequence (welcome, profile detection, session resume, memory load, tool registry build) is split into typed phases, each independently testable.
- **`__version__` now read dynamically from package metadata** (`importlib.metadata.version("aiswmm")`) so `aiswmm --version`, `pyproject.toml`, and the installed wheel can never drift.
- **OpenAI model selection menu removed** from the `curl|bash` installer. The default is locked to `gpt-5.5`; override via `AISWMM_MODEL` env var if needed.

### Fixed

- **`pip install aiswmm` no longer crashes on first import.** PyYAML was an undeclared dependency on the CLI import chain (`agentic_swmm.memory.reference_benchmarks` imports `yaml` at module level), so the wheel could be installed but `aiswmm --version` would `ModuleNotFoundError`. PyYAML is now an explicit dependency in `pyproject.toml`.
- **MCP servers now honour the launcher-supplied `.venv` interpreter.** Ten of eleven MCP servers previously hardcoded `python3` in their `spawn` call; on macOS that often resolves to system Python 3.9, below the project's `requires-python>=3.10` floor, causing `ImportError` on every MCP tool call for users who hadn't activated a `.venv`. All servers now read `process.env.PYTHON || "python3"`, matching the pre-existing `swmm-plot` pattern.
- **`curl|bash` install flow prompts for the OpenAI API key.** The API-key step previously skipped under `--yes` (which `bootstrap.sh` always passes), leaving first-time installers with no key configured and the agent CLI mute. The step now uses `/dev/tty` so it remains interactive even when stdin is piped from `curl`.
- **Unknown CLI verbs are reported as errors.** `aiswmm bogus` or `aiswmm runn` (typo) used to be silently routed to the LLM planner — without an OpenAI key, the user saw `OPENAI_API_KEY is not set` and concluded (incorrectly) that the tool required a key for everything. The CLI now reports `error: unknown command 'runn'. Did you mean 'run'?` with exit code 2; free-form natural-language goals still work via the explicit `aiswmm agent "<goal>"` entrypoint.
- **`docs/installation.md` `aiswmm --provider openai` example corrected** to `aiswmm agent --provider openai "..."`. The top-level CLI has no `--provider` flag; that example was silently dropping the argument.
- **Install step labels match actual behaviour.** Step 4 of `scripts/install.sh` is renamed from "Skill files copy" to "Initialize `~/.aiswmm/` directory" (the step only `mkdir`s; real skill deployment happens later, in `aiswmm setup`). The MCP-server-count footnote ("8 servers") was stale and is now generic ("~11 servers").
- **README pre-release pointer no longer hard-codes a version number.** Pre-release pointers now refer readers to `CHANGELOG.md` for the current version instead of going stale on every alpha bump.

### Notes

- v0.6.4 reproducibility is **unaffected** — every pinned channel (PyPI, Git tag, Docker image) is immutable, and `pip install aiswmm==0.6.4` / `docker pull ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.6.4` still produce byte-identical environments for paper-aligned runs.
- The v0.7.0a1 release notes below are preserved as historical context. The v0.7.0a2 tag carried the tool-registry / runtime-loop refactor wave but never published a changelog of its own; that content is folded into the "Changed" section above.
- The agent runtime stays in its alpha-stage software status as described in the README — the API surface may still evolve before the planned 1.0 release.

## v0.7.0a1 - Modeling memory, Claude Agent SDK provider, CLI/UX overhaul (2026-05-21)

Pre-release (alpha) on top of v0.6.4. Install with `pip install aiswmm==0.7.0a1` or `pip install --pre aiswmm`; the default `pip install aiswmm` still ships v0.6.4. v0.6.4 reproducibility is unaffected — every pinned channel (PyPI, Git tag, Docker image) is immutable.

### Added

- **Modeling-memory substrate.** An on-disk memory layer under `memory/modeling-memory/`: `parametric_memory` (run-level parameters and QA metrics), `calibration_memory` (accepted calibrations and goodness-of-fit), `reference_benchmarks` (library defaults) with a per-project `project_overrides.yaml` overlay, a citation library, and `negative_lessons` (known-bad parameter regions). Includes watershed-similarity matching and SQLite indexing for large stores. Scaffold it with `aiswmm bootstrap memory`.
- **Memory-informed runtime.** The planner can read modeling memory to disambiguate ambiguous requests, adapt QA thresholds to project history, and carry parameter priors across watersheds, with a transparency log of which memory entries were used. Opt out per run with `--ignore-memory`.
- **Claude Agent SDK provider (optional).** A second LLM backend that routes the planner through a Claude Pro/Max subscription via the local `claude` CLI. Install the optional extra with `pip install aiswmm[claude]`. The default OpenAI provider is unchanged and pulls none of this.
- **New CLI verbs:** `aiswmm compare` (per-node / per-subcatchment run diffs), `aiswmm storm` (Chicago / Huff / SCS design hyetographs), `aiswmm trace` (inspect the agent trace), `aiswmm uncertainty plan`, and `aiswmm bootstrap memory`.

### Changed

- **CLI/UX overhaul.** A unified flag convention across every verb (`--inp` / `--json` / `--quiet` / `--example`), grouped `--help` output, differentiated `error: / cause: / hint:` messages, and an honesty layer that detects SWMM `ERROR` output and stub modes instead of reporting false success. SWMM error text is now routed to stderr.
- Calibration workflow closure: batch-aware planning, run-progress reporting, and resource estimation.
- A SWMM solver-version mismatch between a model and the resolved `swmm5` binary is now refused rather than run silently.

### Notes

- This is an alpha. The Claude Agent SDK provider is new and not yet exercised at scale — feedback via GitHub Issues is welcome.
- v0.6.4 remains the latest **stable** release; nothing about it changes.

## v0.6.4 - Byte-reproducibility hardening: pinned `requirements.lock` + auto-built Docker images (2026-05-18)

First stable release on the 0.6.x line since v0.6.1. Closes the three gaps that previously prevented v0.6.x from supporting end-to-end byte-level reproducibility for the companion Agentic SWMM paper.

### Reproducibility

- **New `requirements.lock`** at the repository root. Pins every transitive dependency at the exact versions used to generate the SHA-256 hashes and figures reported in the paper (86 lines, 14 declared top-level packages, Python 3.11). Generated by `pip freeze` against a clean `scripts/requirements.txt` install in a Python 3.11 venv; re-generation steps are documented in the file header.
- **Dockerfile now installs from `requirements.lock`** when present (falling back to `scripts/requirements.txt` for older tags). This guarantees that `docker pull ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.6.4` produces byte-identical dependency trees on every host.
- **`Dockerfile` `AGENTIC_SWMM_REF` default bumped to `v0.6.4`.** SWMM 5.2.4 source pull from USEPA upstream is unchanged.

### CI / release automation

- **`.github/workflows/docker.yml` now auto-triggers on `push` of any tag matching `v*`.** Every Git tag now produces a matching `ghcr.io/zhonghao1995/agentic-swmm-workflow:<tag>` image automatically, plus a `latest` alias that follows the newest tag. The previous `workflow_dispatch`-only flow remains available for ad-hoc rebuilds of historical tags.
- The release commit and tag both pass `tests/test_no_private_machine_paths_in_public_docs.py`, restoring a green CI for the published release tag (v0.6.3a1's release-note file had previously tripped this guard).

### Version metadata consistency

- `pyproject.toml`, `agentic_swmm/__init__.py`, `README.md`, and `docs/installation.md` are all bumped to `0.6.4`.
- `CITATION.cff` `version:` field updated from the stale `0.5.0` to `0.6.4`.
- `README.md` and `docs/installation.md` no longer claim that `pip install aiswmm` ships v0.6.1; they now reflect that v0.6.4 is the default stable target.

### What is NOT changed

- No SWMM engine, Skill, MCP, or CLI behaviour changes. All agent surfaces behave identically to v0.6.3-alpha.
- No QA / audit-gate logic changes. The verification-first provenance contract (Section 2.3.1 of the companion paper) is identical.

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
