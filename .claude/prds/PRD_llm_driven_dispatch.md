# PRD: LLM-driven dispatch refactor

Status: Draft → implementation in progress
Base: `feat/swmmanywhere @ 42e6111`
Owner: refactor branch

## 1. Problem statement

aiswmm v0.7.0a2 introduced a *dispatch layer* between the user's
natural-language prompt and the deterministic SWMM tools:

1. `select_workflow_mode` tool — a hardcoded enum of seven mode names
   (`calibration`, `uncertainty`, `prepared_inp_cli`,
   `full_modular_build`, `existing_run_plot`, `audit_only_or_comparison`,
   `prepared_demo`).
2. `agentic_swmm/agent/workflow_modes/` — one adapter module per
   workflow mode that the planner dispatches to.
3. `agentic_swmm/agent/intent_disambiguator.py` — an LLM-classifier
   helper that re-derives a mode string when a goal looks ambiguous.

The layer was added as a defensive gate against GPT-4-era LLM
hallucination: by funnelling every SWMM goal through one of seven
named modes, the planner could keep behaviour deterministic.

### Why it now hurts more than it helps

- **Maintenance cost.** Adding a new workflow skill (e.g. the new
  `swmm-anywhere` synth-from-bbox path) requires editing *four* places:
  a `workflow_modes/<name>.py` adapter, a `tool_handlers/<name>.py`,
  the `select_workflow_mode` enum, and the `intent_disambiguator`
  prompt. Most of these are pure routing boilerplate — no behaviour
  change.
- **Real failure case.** A user prompt that says
  *"use SWMManywhere to synthesise an INP for this bbox"* currently
  falls through to `audit_only_or_comparison`: the enum does not
  contain a `synth-from-bbox` value and the swmm-anywhere skill never
  registered a tool handler, so the keyword fallback selects the
  closest mode — which has nothing to do with synthesis. Adding the
  new skill within the existing dispatch shape requires touching the
  four places above.
- **LLM-capability mismatch.** Frontier 2026-era LLMs (GPT-5.5,
  Claude Opus 4.7) pick the right function from a tools list with
  ~95% accuracy when each tool's description is well-written. A
  hardcoded mode gate that re-classifies the goal first throws away
  that capability and re-introduces the keyword-matching brittleness
  the LLM is meant to replace.
- **Upstream alignment.** OpenAI's function-calling API and
  Anthropic's tool-use API both assume *LLM directly picks tools from a
  flat tool registry*. Our mode-gated dispatch is the opposite shape:
  one big tool that branches into the real handlers, hiding the real
  tools from the LLM. We are diverging from the industry norm without
  a benefit-side justification.

## 2. Architecture decision

**Before**:
```
User Prompt
   ↓
planner.run() — keyword detection for "swmm-shaped" goal
   ↓
select_workflow_mode tool (forced first hop)
   ↓
workflow_modes/<mode>.py adapter
   ↓
skill script(s) via tool_handlers/<name>.py
```

**After**:
```
User Prompt
   ↓
planner.run() — passes goal + tool_registry to LLM
   ↓
LLM directly picks tool(s) — reads each SKILL.md / tool description
   ↓
tool_handlers/<name>.py (typed param validation — industry-standard)
   ↓
skill script
```

### Decisions

- KEEP `tool_handlers/` — typed-parameter validation per tool is the
  same shape as OpenAI's function-calling API.
- REMOVE `agentic_swmm/agent/workflow_modes/` — the per-mode dispatch
  adapter family is the redundant routing layer.
- REMOVE `select_workflow_mode` tool — the hardcoded gate that pretends
  to be a classifier but is really keyword-matching with extra steps.
- REMOVE `agentic_swmm/agent/intent_disambiguator.py` — its job
  evaporates when `select_workflow_mode` does.
- KEEP `agent/config/intent_map.json` (if present) as a soft hint
  document for LLMs / humans, not as machine-readable routing.

### What does NOT change

- MCP layer (`mcp_client`, `mcp_pool`, `mcp_cache`).
- Audit pipeline (`audit_hook`, `audit/`).
- Memory layer (`memory_*`, `memory_trace`, `recall_memory*`).
- SKILL.md contracts (the LLM still reads them — that is in fact the
  *point* of the refactor).

## 3. Implementation plan

### Phase 1 — Add `swmm_anywhere` tool handler (1 h)

- New `agentic_swmm/agent/tool_handlers/swmm_anywhere.py` mirroring
  `swmm_runner.py`. In-process (not MCP-routed) — it calls
  `agentic_swmm.integrations.swmmanywhere_runner.run_synth_from_bbox`
  directly with typed param validation.
- Tool name: `synth_swmm_from_bbox`.
- Required: `bbox: list[number, 4]`.
- Optional: `run_dir`, `project_name`, `refresh_raw`,
  `upstream_defaults`, `rain_file`.
- Stage-aware error hint reusing the CLI script's pattern
  (`extra_missing` → install hint, `rain_file_missing` → path hint,
  default → smaller-bbox / `--refresh-raw` hint).
- Register in `tool_registry._build_tools()`.
- Add `tests/test_tool_handlers_swmm_anywhere.py`.

### Phase 2 — Remove `select_workflow_mode` gate (2–3 h)

- Delete the `select_workflow_mode` ToolSpec entry from
  `_build_tools()`.
- Delete `agentic_swmm/agent/tool_handlers/workflow_mode.py` (the
  handler module).
- Update `agentic_swmm/agent/prompts.py` `openai_planner_prompt()` to
  remove the "always call select_workflow_mode first" instruction
  and add a "Read SKILL.md descriptions before invoking tools".
- Update `agentic_swmm/agent/planner.py`:
  - Remove the `select_workflow_mode` forced first hop in `run()`.
  - Remove the `_dispatch_workflow_mode` and
    `_classify_plot_continuation` calls into the mode adapters.
- Compatibility: keep the per-call `workflow_mode` *argument* on
  `audit_run` (it is a tagging concern, not a routing concern).

### Phase 3 — Delete `workflow_modes/` + `intent_disambiguator.py` (2 h)

- `rm -rf agentic_swmm/agent/workflow_modes/`.
- `rm agentic_swmm/agent/intent_disambiguator.py`.
- Repair every import:
  - `tool_registry.py`: `_VALID_MODE_ENUM` and the re-exports from
    `tool_handlers.workflow_mode`.
  - `planner.py`: `WorkflowContext`, `get_mode`, the `_helpers`
    re-exports, the `disambiguate` import.
  - `tool_handlers/swmm_audit.py`: any `workflow_modes` reference.
  - `memory_verbs.py`: docstring reference is fine; only remove
    actual `import` lines.
- Re-home any helper still needed (e.g. plot-related extractors from
  `workflow_modes/_helpers.py`) into a non-dispatch module.

### Phase 4 — Test rewrite (3–4 h)

- Delete tests whose entire purpose was the mode-selection logic:
  - `test_workflow_mode_*.py`
  - `test_select_workflow_mode_*.py`
  - `test_intent_disambiguator.py`
  - `test_planner_intent_disambiguation_audit_trail.py`
- Rewrite tests that asserted on tool *dispatch* (not mode selection)
  to instead assert that the LLM picks the right tool given a goal.
- Add `tests/test_llm_driven_dispatch.py` with mocked LLM client:
  - bbox-only prompt → LLM picks `synth_swmm_from_bbox`.
  - prompt referencing an existing run dir → LLM picks
    `plot_run` / `inspect_plot_options`.
- Full suite green (ignoring the same heavy/QGIS opt-out list the
  CI baseline already ignores).

### Phase 5 — Docs + CHANGELOG (1 h)

- `CONTEXT.md`: append an ADR-style section
  *"Dispatch architecture decision: LLM-driven over hardcoded mode
  enum"* after the existing "Real-data path vs Synth-data path".
- `CHANGELOG.md` `## Unreleased` → `### Changed`:
  - what was removed (workflow_modes/, select_workflow_mode,
    intent_disambiguator);
  - what was added (swmm_anywhere tool handler, LLM-driven dispatch
    contract);
  - migration impact (interactive behaviour unchanged for end users;
    the planner is just smarter at picking tools);
  - upstream alignment (OpenAI / Anthropic tools API shape).
- Append "Implementation status: completed in commits X, Y, Z" to
  this PRD.

## 4. Test strategy

| Layer | Test |
| --- | --- |
| Unit, new handler | `tests/test_tool_handlers_swmm_anywhere.py` — typed-param validation, stage-aware error mapping, success path through a stubbed `run_synth_from_bbox`. |
| Integration, LLM-driven dispatch | `tests/test_llm_driven_dispatch.py` — mock provider returns specific tool calls; assert planner forwards them through `tool_handlers/`. |
| Regression, surviving handler tests | `tests/test_tool_handlers_shared_helpers.py`, `tests/test_tool_handlers_skill_family_mapping.py`, the family-specific handler tests — must remain green. |
| Whole suite | `python3.11 -m pytest` with the existing CI opt-out list — same pass-rate as base or better. |

## 5. Migration / backward compat

- **CLI behaviour** is unchanged. Users continue to talk to the
  interactive shell. They never typed `select_workflow_mode`
  themselves; only the planner did, and the planner is the surface
  this PRD repaints.
- **External callers** of the agent module (none known outside this
  repo) — anyone who imported `agentic_swmm.agent.workflow_modes` or
  `agentic_swmm.agent.intent_disambiguator` will need to update; the
  CHANGELOG documents the rename / removal.
- **Audit trail** — `audit_run` keeps its `workflow_mode` argument
  for tagging.

## 6. Out of scope

- MCP layer changes.
- Audit pipeline changes.
- Memory layer changes.
- SKILL.md contract rewrites (we keep them; their descriptions are
  what the LLM now reads).
- Provider abstraction changes (planner still talks to ChatProvider).

## 7. Implementation status

Completed on the `feat/swmmanywhere`-based refactor branch in six
commits:

| Phase | Commit | Title |
| --- | --- | --- |
| 0 | `23cc880` | docs(prd): LLM-driven dispatch refactor |
| 1 | `02f6d8b` | feat(tool_handlers): add swmm_anywhere typed-tool surface for LLM-driven dispatch |
| 2 | `ebd4dae` | feat(agent): remove select_workflow_mode hardcoded gate; LLM picks tools directly |
| 3 | `15548e8` | refactor(agent): delete workflow_modes/ and intent_disambiguator — LLM reads SKILL.md directly |
| 4 | `919d97b` | test(agent): rewrite workflow_mode tests around LLM-driven dispatch; add LLM-mock integration tests |
| 5 | *this commit* | docs: record LLM-driven dispatch refactor (ADR + CHANGELOG + PRD status) |

Verification (full suite with the standard CI opt-out list applied):

- 2140 tests passing, 12 skipped.
- 14 pre-existing test failures (geo / MCP / outfall script bugs)
  are the same set that fails on `feat/swmmanywhere @ 42e6111` —
  unrelated to this refactor.
- The new integration suite `tests/test_llm_driven_dispatch.py`
  pins the post-refactor contract (LLM picks tool by name, call
  reaches the executor, `select_workflow_mode` never appears in
  the plan).

Net diff vs `feat/swmmanywhere @ 42e6111`: ~30 files changed,
1 new tool handler + 1 new integration test added, 12 dispatch-layer
modules and 13 dispatch-layer test files deleted, ~1100 LOC removed.
