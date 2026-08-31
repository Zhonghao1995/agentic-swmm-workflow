# Repository Map

This repository is the Agentic SWMM workflow layer: the `aiswmm` runtime, twenty workflow-stage skills, eleven MCP servers, and the test suite that keeps their contracts honest. The public repository is:

```text
Zhonghao1995/agentic-swmm-workflow
```

## The four layers

| Layer | Where | What it does |
|---|---|---|
| Runtime | `agentic_swmm/` | The pip-installable package (`pip install aiswmm`). Registers the CLI verbs, runs the LLM planner loop, enforces permissions and HITL gates, and manages memory and providers. |
| Skills | `skills/` | Twenty skills, each a `SKILL.md` contract plus `scripts/`. The domain logic lives in these scripts; both CLI verbs and agent tools execute them. |
| MCP servers | `mcp/` | Eleven Node stdio servers that wrap the same skill scripts for external agent runtimes (Codex, Claude, OpenClaw, Hermes). |
| Tests | `tests/` | The largest layer by volume. Contract, drift-guard, and integration tests over the other three. |

One table binds the layers: `EXPECTED_BINDINGS` in `agentic_swmm/agent/mcp_coverage.py` maps each typed agent tool to its skill script and its MCP server/tool, so the same fact is never maintained twice.

## Top-level folders

| Folder | Role |
|---|---|
| `agentic_swmm/` | Python runtime and CLI (see below). |
| `skills/` | Workflow-stage skills: `SKILL.md` + `scripts/`, some with `examples/` and `tests/`. |
| `mcp/` | MCP servers, one directory per server, plus the shared `_lib/` prologue. |
| `tests/` | Top-level test suite. |
| `scripts/` | Installers and bootstrap, benchmarks, acceptance runner, MCP config generation. |
| `agent/` | Startup memory files the planner loads, plus `config/intent_map.json` (keyword-to-skill routing hints). |
| `memory/modeling-memory/` | Generated modeling memory derived from audited runs. |
| `examples/` | Small reusable input fixtures and prepared cases. |
| `cases/` | Public case studies with their figures and sample deliverables. |
| `data/` | Raw GIS inputs backing the bundled cases. |
| `docs/` | Human-readable documentation (entry points below). |
| `runs/` | Generated outputs; not committed. `runs/README.md` documents where new runs land. |
| `web/` | The one-line installer scripts served from aiswmm.com. |
| `integrations/` | Setup guidance for wiring the MCP servers and skills into external runtimes. |

## Runtime (`agentic_swmm/`)

| Subpackage | Role |
|---|---|
| `agent/` | Planner loop, tool registry, skill router, permissions and profiles, HITL surface, gap-fill runtime, session bootstrap, SWMM runtime helpers. |
| `commands/` | One module per CLI verb; `expert/` holds the operator-only authority verbs. |
| `memory/` | Cross-run memory: parametric records, lessons lifecycle, recall, session store. |
| `providers/` | LLM providers (openai default, anthropic opt-in), standard-library HTTP clients. |
| `gap_fill/` | Detect-propose-review-record loop for missing inputs. |
| `hitl/` | Threshold evaluator and expert-review pause. |
| `audit/` | Run-folder invariants, provenance records, the Obsidian MOC generator. |
| `integrations/` | SWMMCanada and SWMManywhere upstream runners. |
| `diagnostics/` | `aiswmm doctor` report and fixes. |
| `case/`, `reporting/`, `runtime/`, `utils/` | Case registry, run README rendering, resource registry, shared helpers. |

The CLI registers 33 verbs in seven help groups (Core workflow, Analysis, Memory, Expert, Inspection, Case namespace, Setup). Any input that is not a registered verb routes to the agent and its LLM planner. The expert authority verbs (`aiswmm expert calibration accept`, `pour_point confirm`, `thresholds override`, `publish`, and related) are deliberately not exposed as agent tools; a test pins that boundary.

## Skill layer

Skills are grouped by workflow stage, not by algorithm. New methods should become scripts, examples, or strategy options inside an existing stage skill rather than new skills.

| Skill | Main question |
|---|---|
| `swmm-gis` | How are subcatchment inputs derived from the user's GIS and DEM layers? |
| `swmm-network` | How are junctions, conduits, and outfalls built and checked from raw network data? |
| `swmm-params` | How do land use and soils map to SWMM runoff and infiltration parameters? |
| `swmm-climate` | How is rainfall formatted, and how are design storms generated? |
| `swmm-builder` | How is a runnable INP assembled from prepared artifacts? |
| `swmm-runner` | How is SWMM executed reproducibly and its report parsed? |
| `swmm-plot` | How are hydrographs and network maps rendered from a run? |
| `swmm-calibration` | Which parameters best match observations? |
| `swmm-uncertainty` | How much output spread follows from uncertain inputs? |
| `swmm-lid-optimization` | Which LID scenario choices improve objectives? |
| `swmm-water-quality` | What pollutant loads does a run report? |
| `swmm-design-review` | Does a run comply with a design rulebook? |
| `swmm-report` | How does an audited run become a client Word deliverable? |
| `swmm-experiment-audit` | What happened in one run, and what evidence supports it? |
| `swmm-modeling-memory` | What keeps happening across audited runs? |
| `swmm-rag-memory` | How is past modeling memory retrieved for a new question? |
| `swmm-canada` | How is a ready-to-run model fetched for a Canadian area from the SWMMCanada upstream? |
| `swmm-anywhere` | How is a plausible network synthesized from OSM and DEM data where no pipe data exists? |
| `swmm-end-to-end` | Which module should run next in an agent-orchestrated workflow? |
| `skill-author` | How is a new skill scaffolded from a described recurring need? |

## MCP servers

Eleven stdio servers: `swmm-builder`, `swmm-calibration`, `swmm-climate`, `swmm-experiment-audit`, `swmm-gis`, `swmm-modeling-memory`, `swmm-network`, `swmm-params`, `swmm-plot`, `swmm-runner`, `swmm-uncertainty`. Each is a thin wrapper that spawns the corresponding skill script and returns its output. Generate runtime configs with `node scripts/generate_mcp_configs.mjs` and smoke-test discovery with `node scripts/smoke_mcp_servers.mjs`; see `integrations/` for per-runtime guidance.

## Run layout

New sessions land under `runs/<YYYY-MM-DD>/<HHMMSS>_<case>_run/` (a goal that executed tools) or `..._chat/` (a conversational turn). Inside a run, the canonical numbered stages `00_raw` through `11_review` are defined in `agentic_swmm/agent/swmm_runtime/run_layout.py`. The one enforced invariant: an audited run carries `09_audit/experiment_note.md` and `09_audit/experiment_provenance.json` (`agentic_swmm/audit/run_folder_layout.py`). `runs/INDEX.md` is a regenerated map of content; legacy folders are read-only forever. Details: `runs/README.md`.

## Memory and audit layers

| Layer | Path | Job |
|---|---|---|
| Startup memory | `agent/memory/` | Identity and operating posture the planner loads into its system prompt. |
| Modeling memory | `memory/modeling-memory/` | Generated summaries of audited runs: lessons, parametric records, proposals. |
| Session evidence | `runs/**/agent_trace.jsonl` and `09_audit/` | Per-session event log and derived audit records. |

The audit layer feeds modeling memory:

```text
SWMM run -> swmm-experiment-audit -> 09_audit/ -> swmm-modeling-memory
```

Modeling memory can propose skill updates, but proposals are evidence-gated (a pattern must recur across at least three runs) and always human-approved. Audit records are evidence for a run; modeling memory is a summary of repeated patterns; neither proves a scientific claim by itself.

## Documentation entry points

| Document | Use when |
|---|---|
| `docs/installation.md`, `docs/runtime-install-options.md` | You are installing the runtime or choosing an install path. |
| `docs/install-troubleshooting.md` | An install failed and you need the known fixes. |
| `docs/validation-evidence.md` | You need benchmark evidence boundaries and runnable verification paths. |
| `docs/experiment-audit-framework.md` | You need the audit artifact contracts. |
| `docs/calibration-uncertainty-workflow.md` | You need calibration and uncertainty boundaries. |
| `docs/climate-scenarios.md` | You need the climate-forcing workflow. |
| `docs/hitl-thresholds.md` | You need the QA thresholds that trigger expert review, with their rationale. |
| `docs/modeling-memory-and-skill-evolution.md` | You need the modeling-memory and skill-evolution rules. |
| `docs/byte-identical-reproducibility.md` | You need the reproducibility statement and its scope. |
| `docs/llm_providers.md` | You are configuring an LLM provider route. |
| `docs/swmm-anywhere-quickstart.md` | You are synthesizing a network from a bbox. |
| `docs/openclaw-execution-path.md`, `docs/codex-runtime.md` | You are driving the skills from an external agent runtime. |

## Evidence boundary

The repository is strongest as a reproducible, auditable workflow for:

- prepared-input SWMM execution;
- real storm networks fetched from the SWMMCanada upstream inside Canada, and SWMManywhere synthesis elsewhere;
- calibration, validation, and uncertainty propagation;
- audit records and modeling-memory summaries.

Do not overstate it as fully automatic greenfield watershed and pipe-network generation unless a case-specific benchmark has validated those inputs, outputs, and QA checks.
