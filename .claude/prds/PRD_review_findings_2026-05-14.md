# Architecture review findings — 2026-05-14

Reviewer: read-only audit agent. Branch: `review/architecture-2026-05-14`. HEAD: `d8e76bc` (post-#77 + the un-#-numbered ME-3 reflect CLI). 17 commits added on top of the prior architectural baseline tonight.

Baseline check: `pytest -q` reports 538 passed / 18 failed / 13 skipped (3 collection-error test files excluded for missing `geopandas`/`spotpy` in this audit env). Failures are not introduced by tonight's work — they trace to optional-dep gaps (geopandas, spotpy, shapely subprocess paths) and to pre-existing dynamic-import shims that re-shell into `sys.executable -m pytest`. Out of scope for this review.

## Executive summary

10 findings: **2 P0 / 5 P1 / 3 P2**. Headline themes:

1. **Public/private boundary has drifted.** `.gitignore` still labels `skills/swmm-rag-memory/`, `memory/rag-memory/*`, and `docs/obsidian-compatible-rag-memory.md` as "paper IP — do NOT commit", yet all of them are tracked in `main`. The `check_package_boundary.py` wheel gate would actively reject these in a build artifact. That is a release-blocker contradiction.
2. **Workflow-mode routing has broken Chinese input.** `select_workflow_mode` in `agentic_swmm/agent/tool_registry.py` contains literal `"??"` / `"???"` ASCII strings where the original Chinese keywords (校准, 不确定性, 比较, 演示) used to be. Goal routing for Chinese-only goals now matches anything that contains `??`.
3. **`agent/memory/` advertises more startup memory than the registry actually loads.** README load order names 5+ files; `LONG_TERM_MEMORY_FILES` in `agentic_swmm/runtime/registry.py` only registers 3.

Detailed findings below.

---

## P0-1. Paper-IP gitignore lists files that are tracked in `main`

**Priority:** P0 (release blocker / public boundary integrity).

**Evidence (files):**

- `.gitignore:79-122` lists `skills/swmm-lid-optimization/`, `skills/swmm-rag-memory/`, `docs/obsidian-compatible-rag-memory.md`, `memory/rag-memory/`, `memory/modeling-memory/projects/`, `memory/modeling-memory/run_memory_summaries.json`, `tests/test_swmm_rag_memory.py`, `tests/test_qgis_mcp_contracts.py`, plus all entropy-paper scripts/tests — under the header `# Paper IP — local-only files, do NOT commit (until papers published).`
- `git ls-files` returns these for currently-tracked files matching that list:
  - `skills/swmm-rag-memory/SKILL.md`
  - `skills/swmm-rag-memory/scripts/answer_with_memory.py`
  - `skills/swmm-rag-memory/scripts/build_memory_corpus.py`
  - `skills/swmm-rag-memory/scripts/generate_failure_advice.py`
  - `skills/swmm-rag-memory/scripts/rag_memory_lib.py`
  - `skills/swmm-rag-memory/scripts/record_resolution_memory.py`
  - `skills/swmm-rag-memory/scripts/refresh_after_run.py`
  - `skills/swmm-rag-memory/scripts/retrieve_memory.py`
  - `memory/rag-memory/corpus.jsonl`
  - `memory/rag-memory/embedding_index.json`
  - `memory/rag-memory/keyword_index.json`
  - `docs/obsidian-compatible-rag-memory.md`
- `scripts/check_package_boundary.py:FORBIDDEN_SUBSTRINGS` lists the same paths — meaning `python -m build` followed by the boundary check would fail on any tarball/wheel built from `main` because these files would be included.

**Why this matters:** The gitignore header is the canonical "do these go in the public repo?" contract. Either the rag-memory skill (and its bundled corpus/index) was intentionally promoted to public — in which case the gitignore + boundary script need to drop those paths — or the commit that promoted them (8d6e948 in #36) was a slip that needs reverting. Right now `main` is internally inconsistent and the wheel boundary check is a tripwire waiting to fire on the next release build.

**Proposed fix:**

1. Decide policy: rag-memory is **public** (keep tracked) vs **paper-IP-private** (untrack).
2. If public: remove the matching lines from both `.gitignore` (lines 111-119) and `scripts/check_package_boundary.py:FORBIDDEN_SUBSTRINGS`.
3. If private: `git rm --cached -r skills/swmm-rag-memory/ memory/rag-memory/ docs/obsidian-compatible-rag-memory.md` and revisit downstream imports in `agentic_swmm/memory/recall_search.py`, `agentic_swmm/agent/tool_registry.py` (the `recall_memory_search` tool), and `agentic_swmm/runtime/registry.py:MODELING_MEMORY_FILES`.

Recommendation: option (1) — public is what main has shipped since #36; clean the gitignore. The boundary script should keep entries for the still-private skills (`swmm-lid-optimization`, entropy scripts) and drop the rag-memory entries.

---

## P0-2. `select_workflow_mode` Chinese-keyword routing has been broken to `"??"` placeholders

**Priority:** P0 (silent functional regression).

**Evidence (file:line):**

`agentic_swmm/agent/tool_registry.py:860-922`:

```python
wants_calibration = any(word in goal for word in ("calibration", "calibrate", "observed", "nse", "kge", "??", "??"))
wants_uncertainty = any(word in goal for word in ("uncertainty", "fuzzy", "sensitivity", "???", "??"))
wants_audit = "audit" in goal or "comparison" in goal or "compare" in goal or "??" in goal or "??" in goal
wants_demo = any(word in goal for word in ("demo", "acceptance", "??", "??"))
if "compare" in goal or "comparison" in goal or "??" in goal:
```

These `"??"`/`"???"` tokens are literal ASCII strings in the source file (verified by reading raw bytes). They are leftover placeholders from a non-UTF-8 sync (likely the public/private sync in `c35f5e4`). Two effects:

- Bilingual goal routing is dead. A Chinese-only user prompt like "请帮我做不确定性分析" will never match because the language-specific keywords have been replaced.
- Worse, **every** Chinese prompt containing literal `??` (e.g., a user pasting question marks) now triggers `wants_calibration` AND `wants_uncertainty` AND `wants_audit` AND `wants_demo` simultaneously. The first branch (`existing_run_plot`) is the most common short-circuit so the impact is partly masked, but `wants_demo` would fire on any prompt containing `??`.

**Proposed fix:** restore the Chinese keyword strings that used to be in this branch. Best minimal-risk path: `git log --all --diff-filter=D` to find the most recent UTF-8 version of these lines, then restore. A reasonable guess at the original set (verify against history before merging):

```python
wants_calibration = any(word in goal for word in ("calibration", "calibrate", "observed", "nse", "kge", "校准", "率定"))
wants_uncertainty = any(word in goal for word in ("uncertainty", "fuzzy", "sensitivity", "不确定性", "敏感性"))
wants_audit = "audit" in goal or "comparison" in goal or "compare" in goal or "审计" in goal or "比较" in goal
wants_demo = any(word in goal for word in ("demo", "acceptance", "演示", "验收"))
if "compare" in goal or "comparison" in goal or "比较" in goal:
```

Add a regression test (`tests/test_select_workflow_mode_chinese_keywords.py`) so the next non-UTF-8 sync trips before merge.

---

## P1-1. `LONG_TERM_MEMORY_FILES` registry loads only 3 of the 6+ memory files promised by the README

**Priority:** P1.

**Evidence:**

- `agent/memory/README.md:7-22` lists a "recommended load order" of: identification_memory.md, operational_memory.md, evidence_memory.md, `skills/swmm-end-to-end/SKILL.md`, `docs/openclaw-execution-path.md`, then optional: soul.md, modeling_workflow_memory.md, user_bridge_memory.md, `skills/swmm-modeling-memory/SKILL.md`, `memory/modeling-memory/`.
- `agentic_swmm/runtime/registry.py:27-31`:

  ```python
  LONG_TERM_MEMORY_FILES = [
      ("agent/memory/identification_memory.md", "agent/identification_memory.md"),
      ("agent/memory/operational_memory.md",   "agent/operational_memory.md"),
      ("agent/memory/evidence_memory.md",      "agent/evidence_memory.md"),
  ]
  ```

- `enabled_startup_memory_files()` (line 171) only returns records with `load_at_startup=True`, which is set only for `LONG_TERM_MEMORY_FILES`. The remaining tracked files — `soul.md` (50 LOC), `modeling_workflow_memory.md` (211 LOC, the most detailed boundary-discipline file with the explicit calibration gate), `user_bridge_memory.md` (98 LOC) — exist on disk but are **never** auto-injected.

**Why this matters:** `modeling_workflow_memory.md` is where the per-step calibration/validation boundary discipline lives ("I won't claim a model is calibrated unless these checks pass"). If the agent runtime loads only identification + operational + evidence, the runtime is operating without the workflow-specific gate language at startup. README readers (and other agent runtimes following the README) will assume more is loaded than is.

**Proposed fix:** decide policy + reconcile. Two clean options:

1. **README is the truth.** Expand `LONG_TERM_MEMORY_FILES` to include `modeling_workflow_memory.md` and `user_bridge_memory.md` (mark `soul.md` optional with `load_at_startup=False` and rely on memory-recall to surface it). Update the registry-discovery test (if one exists; otherwise add one) so the count drift can't reopen.
2. **Registry is the truth.** Update `agent/memory/README.md` to clearly mark items 6-9 as "optional, not auto-injected — load on demand via `recall_memory`/manual reference."

Recommendation: (1) — the boundary phrases in `modeling_workflow_memory.md` are not safely dropped from the startup prompt.

---

## P1-2. `rag_memory_lib` carries a hashed-embedding + char-n-gram path that is mostly unused for a 26-entry corpus

**Priority:** P1.

**Evidence:**

- `skills/swmm-rag-memory/scripts/rag_memory_lib.py` is 761 LOC (the brief said 1520 — looks like that figure included `agentic_swmm/memory/{lessons_metadata,lessons_lifecycle,session_db,facts,audit_hook,recall_search}.py` together; the individual lib file is 761).
- `memory/rag-memory/corpus.jsonl` contains 26 entries (`wc -l`), totalling ~3,500 tokens across 26 dict records (avg text length 1,205 chars).
- `embedding_index.json` is 384-dim hashed embedding (`EMBEDDING_DIMENSIONS = 384`), with ~378 non-zero entries per 26 vectors (~98% dense — the hashing collides almost completely on a 26-entry corpus).
- The `retrieve()` function (rag_memory_lib.py:465) defaults `retriever="keyword"`. The semantic / hybrid path (lines 481-499) only fires when callers pass `retriever="hybrid"`. Searching the repo:

  ```
  $ grep -rn 'retriever="hybrid"\|retriever=.hybrid' --include="*.py"
  # no caller wires hybrid explicitly
  ```

  `recall_memory_search` (`agentic_swmm/memory/recall_search.py`) calls `rag_memory_lib.retrieve(...)` without a `retriever=` kwarg → keyword path only.
- The cost of writing the embedding index on every refresh is borne (`write_corpus` always emits both `keyword_index.json` and `embedding_index.json`), even though nothing consumes the embedding file in production.

**Why this matters:** 384-dim hashed embeddings with bi-gram + char-3/4/5-gram feature space is over-engineering for 26 corpus entries. The keyword index + the `QUERY_EXPANSIONS` Chinese→English fallback table already provides reasonable recall. Char-n-gram is real machinery that needs to be understood and tested. For 26 entries, exact-token matching plus the existing expansion dict is sufficient.

**Proposed fix:** two-stage trim, not a rewrite:

1. **Stage 1 (low-risk, this fix-agent cycle).** Feature-flag the embedding path. Don't compute/write `embedding_index.json` unless `retriever="hybrid"` is enabled. Save IO on every audit. Drop `EMBEDDING_DIMENSIONS`, `hashed_embedding`, `cosine_sparse`, `char_ngrams`, `embedding_features`, `HYBRID_*_WEIGHT` from the always-imported surface — move to a sub-module `rag_memory_lib_semantic.py` imported only when hybrid is wired.
2. **Stage 2 (future, post-paper).** Re-evaluate whether to ship a real embedding backend (sentence-transformers via a pluggable interface) or stay keyword-only and remove the semantic path entirely.

Expected savings: ~250 LOC out of the 761 in stage 1.

---

## P1-3. `install.ps1` ships flags that no one tested + bash/ps1 flag naming diverges

**Priority:** P1.

**Evidence:**

- `scripts/install.ps1` declares `[switch]$SkipSwmm`, `[switch]$SkipSetup`, `[string]$SwmmVersion = "5.2.4"` (lines 30-34) and **never references them anywhere else in the file** (verified by grep). Same applies to `scripts/install.sh` (`SKIP_SWMM`, `SKIP_SETUP`, `SWMM_REF`) — declared, parsed from argv, never read by any step.
- The flag names diverge between platforms:
  - bash: `--swmm-ref`, env `SWMM_REF`, default `v5.2.4` (git ref).
  - ps1: `-SwmmVersion`, default `5.2.4` (version string, no `v` prefix).
  Anyone scripting `install.{sh,ps1}` from a shared deployment recipe will break on either.
- Test coverage:

  ```
  $ ls tests/test_install*
  tests/test_install_script_prompts.bash
  tests/test_install_script_prereq_python.bash
  tests/test_install_script_prereq_node.bash
  ```

  Three bash harness tests. **Zero `install.ps1` tests.** No PowerShell harness in the repo. The agent that wrote #71 confirmed ps1 was not exec-tested.
- Behavioural gaps:
  - ps1's `Do-ApiKey` writes `$AiswmmEnvFile` (`~/.aiswmm/env.ps1`) but never sets restrictive ACL. bash's `do_api_key` does `chmod 600`. The API key file is world-readable on Windows by default.

**Proposed fix:**

1. Either implement the SWMM-install step (so `--skip-swmm` does something real) or drop the dead flags from both scripts. Decide now — `--skip-swmm` in the help text is a documentation lie.
2. Normalise: pick one of `--swmm-ref/--swmm-version` across both scripts, document the value semantics (git ref vs version), keep the second as a deprecated alias.
3. Add a minimum smoke test for ps1: run `pwsh -File scripts/install.ps1 -Auto -SkipPython -SkipMcp -SkipSetup` in `AISWMM_SKIP_REAL_TOOLS=1` mode and assert exit code 0 + presence of `$AiswmmConfigDir`. Requires `pwsh` available in CI (one-line `apt install powershell` on Ubuntu).
4. On `Do-ApiKey`, set `(Get-Acl $AiswmmEnvFile)` to current-user only after write. Match bash's `chmod 600` semantically.

---

## P1-4. Generic vs domain memory: `agentic_swmm/memory/` mixes session/facts (generic) with lessons/SWMM-audit (domain) in one namespace

**Priority:** P1 (architecture; not a runtime bug).

**Evidence:**

- `agentic_swmm/memory/` currently contains (LOC each, deduped):
  - **Generic / Hermes-delegable:** `session_db.py` (603), `facts.py` (276), `context_fence.py` (137), `session_sync.py` (176), `recall.py` (62) = ~1,250 LOC.
  - **SWMM-domain:** `lessons_metadata.py` (362), `lessons_lifecycle.py` (308), `audit_hook.py` (413), `audit_to_memory.py` (159), `moc_generator.py` (151), `proposal_skeleton.py` (147), `recall_search.py` (212, thin wrapper around domain rag-memory lib), `case_inference.py` (57) = ~1,810 LOC.
- `agentic_swmm/memory/__init__.py` is 3 lines, no API surface differentiation — every caller imports the leaf module directly. So callers can't tell which slice they're depending on.

**Why this matters:** the Hermes critique was correct. If you wanted to factor a `hermes-memory` plugin out tomorrow (sessions + facts + fence + recall — the generic SQLite-FTS5 + curated-facts injection pipeline), there is no current package boundary to lift along.

**Proposed fix:** introduce two sub-packages under `agentic_swmm/memory/`. **Do not move code yet.** Just relabel:

```
agentic_swmm/memory/
    generic/            # symlink or re-export package
        session_db.py
        facts.py
        context_fence.py
        session_sync.py
        recall.py
    domain/
        lessons_metadata.py
        lessons_lifecycle.py
        audit_hook.py
        audit_to_memory.py
        moc_generator.py
        proposal_skeleton.py
        recall_search.py
        case_inference.py
    __init__.py         # re-export both sub-packages for backwards compat
```

Migration path: stage 1 = add re-export shims so `from agentic_swmm.memory import session_db` still works. Stage 2 (separate PR) = update callers to import from `.generic`/`.domain` explicitly. Stage 3 (future) = the `.generic` package can be lifted to a Hermes plugin with one git mv.

Expected effort: stage 1 is ~30 lines, zero runtime risk. Stages 2-3 are out of scope for the fix agent — flag them as follow-on issues.

---

## P1-5. `record_fact` is marked `is_read_only=False` (correct) but `capabilities` and `select_workflow_mode` are `is_read_only=False` (defaults) even though they read only

**Priority:** P1.

**Evidence:** `agentic_swmm/agent/tool_registry.py`:

- Line 249: `ToolSpec("capabilities", "Describe what this runtime can and cannot access.", _object({}), _capabilities_tool)` — handler at line 1344 reads `sorted(_build_tools())` and calls `capability_summary`. No filesystem writes, no subprocess. Should be `is_read_only=True`.
- Line 376: `ToolSpec("select_workflow_mode", ..., _select_workflow_mode_tool)` — handler at line 856 only computes a plan dict from `call.args`, with one read of `runtime_state_path()` for the active-run-dir hint (line 887-888). No writes. Should be `is_read_only=True`.
- Confirmed read-only-correct: `git_diff`, `inspect_plot_options`, `list_dir`, `list_mcp_servers`, `list_mcp_tools`, `list_skills`, `read_file`, `read_skill`, `recall_memory`, `recall_memory_search`, `recall_session_history`, `search_files`, `select_skill`, `web_fetch_url`, `web_search`.
- Correctly write-flagged: every deterministic-SWMM tool (`build_inp`, `run_swmm_inp`, `audit_run`, `summarize_memory`, `plot_run`, `network_qa`, `network_to_inp`, `format_rainfall`), plus `record_fact`, `request_expert_review`, `apply_patch`, `demo_acceptance`, `doctor`, `run_allowed_command`, `run_tests`, `call_mcp_tool`.

**Why this matters:** `Profile.QUICK` auto-approves only `is_read_only=True` tools. `capabilities` and `select_workflow_mode` are both prompt-driven discovery tools the planner calls early; if a user is in QUICK profile and these are not auto-approved, they get an interactive prompt for what is really a no-op read. That's measurable user friction.

**Proposed fix:** add `is_read_only=True` to both ToolSpec calls. Add a test (`tests/test_tool_registry_readonly_inventory.py`) listing every tool name and asserting its read-only flag matches an explicit expected dict so future drift trips a unit test.

---

## P2-1. `_demo_acceptance_tool` and `_doctor_tool` still subprocess `python -m agentic_swmm.cli`

**Priority:** P2 (intentional + already allow-listed; documenting for clarity).

**Evidence:**

- `tests/test_handler_lockin_no_direct_subprocess.py` allow-lists `_demo_acceptance_tool` and `_doctor_tool` along with `_apply_patch_tool`, `_git_diff_tool`, `_run_tests_tool`, `_run_allowed_command_tool`.
- Both shell out via `_run_cli_tool` → `subprocess.run([sys.executable, "-m", "agentic_swmm.cli", ...])`.

**Why this is P2 not P1:** these are operator commands that legitimately wrap the CLI (`aiswmm doctor`, `aiswmm demo acceptance`). They are not deterministic-SWMM stages, so they should remain in-process subprocess; the lock-in test explicitly accepts this. The audit just needs to make sure new tools in this lane stay tagged correctly.

**Proposed fix (optional):** add a docstring comment to `_run_cli_tool` saying "only operator-CLI wrappers should call this; deterministic-SWMM handlers must use `_make_mcp_routed_handler`". Otherwise no change.

---

## P2-2. UX-phase env var support is asymmetric

**Priority:** P2.

**Evidence:**

| Phase | NO_COLOR | AISWMM_DISABLE_WELCOME | First-run marker |
|---|---|---|---|
| install (#71) | NO (helpers.bash only checks `[[ -t 1 ]]`) | n/a | n/a |
| welcome (#72) | YES (`ui_colors.colorize` reads `NO_COLOR`) | YES (`welcome._is_disabled`) | YES (`AISWMM_CONFIG_DIR/.first_run_done`) |
| spinner (#73) | n/a (no colour used) | NO (no env check) | n/a |
| warm identity (#74) | n/a | YES (`runtime_loop._welcome_disabled`) | n/a |
| living MOC (#75) | n/a | NO | n/a |

So a user who sets `AISWMM_DISABLE_WELCOME=1` to suppress greetings still gets a spinner and still writes `runs/INDEX.md` on every session-end. Reasonable for an interactive user, surprising for a CI/headless caller.

**Proposed fix:** add one shared helper, say `agentic_swmm.utils.ux.is_headless()`, that returns True when any of `AISWMM_DISABLE_WELCOME=1`, `AISWMM_HEADLESS=1`, or `NO_COLOR` is set, and gate the spinner + MOC regen + welcome on it. Document in the README that `AISWMM_HEADLESS=1` is the one switch a CI caller needs.

---

## P2-3. Bonus: CHANGELOG drift, dead imports, stale workflow router

**Priority:** P2.

**Evidence:**

1. `CHANGELOG.md` last released entry is `v0.5.0`. `pyproject.toml:version = "0.6.1"`. The "Unreleased" section is empty. Tonight's 17 PRs (the calibration, uncertainty, UX, memory evolution work) are not in the changelog.
2. Dead imports:
   - `agentic_swmm/agent/tool_registry.py:27`: `script_path` imported, never used (every deterministic handler now routes through `_make_mcp_routed_handler`; the script-path helper was only needed by the old subprocess shims).
   - `agentic_swmm/commands/skill.py:4`: `Path` imported, never referenced.
3. `<!-- HYDROLOGY-TODO -->` placeholders in `docs/hitl-thresholds.md` (7 instances, lines 11/19/27/35/43/51/59). These are by-design — they wait for hydrologist sign-off — but they aren't tracked as a separate issue. Worth one issue ("hydrologist to fill rationale fields").

**Proposed fix:**

- CHANGELOG: add `## v0.6.0` and `## v0.6.1` sections grouping tonight's PRs by area (uncertainty, calibration, UX, memory evolution, governance). Move "Unreleased" up to the new HEAD.
- Drop dead imports.
- Open a tracking issue for the HYDROLOGY-TODO list.

---

## Suggested fix order

The fix-agent should do these in roughly this sequence, because the later items depend on the earlier ones not being yanked from under them.

1. **P0-1** (gitignore vs tracked files). Decide policy first — if rag-memory goes private, P1-2 changes shape (no Stage 1 trim because the whole file moves). 30 minutes.
2. **P0-2** (Chinese keyword restore). Independent, fast. 30 minutes including a regression test.
3. **P1-5** (read-only flag fixes for `capabilities` + `select_workflow_mode`). 15 minutes with a registry-inventory test.
4. **P1-1** (memory registry vs README). Pick policy → wire either registry or doc. 1-2 hours.
5. **P2-3** (CHANGELOG + dead imports). 30 minutes.
6. **P1-3** (install scripts). Drop dead flags, fix flag naming, ACL the env.ps1, add a smoke test. 1-2 hours.
7. **P1-2** (rag_memory_lib trim). Only after P0-1 is decided. Stage 1 only — feature-flag the embedding path. 1-2 hours.
8. **P1-4** (generic/domain memory split). Stage 1 re-export shim only. 30 minutes.
9. **P2-1** (docstring on `_run_cli_tool`). 5 minutes; optional.
10. **P2-2** (`AISWMM_HEADLESS` env). 30 minutes; bundle with the install-script flag cleanup since both touch UX-discoverability.

Total expected: ~6-9 hours of fix work split across these tickets, none of them blocked on a separate human-research input.

## Out of scope (noticed but explicitly NOT recommending fixes now)

- **HYDROLOGY-TODO placeholders in `docs/hitl-thresholds.md`.** These are research-judgment calls. Not a code bug. Track as a separate non-engineer issue.
- **18 failing tests on this audit env.** All trace to optional deps (geopandas, spotpy) or to module-import-time subprocess shims that re-shell pytest. Not introduced tonight; pre-existing. Leave to a separate test-env hardening pass.
- **Rag retrieval algorithm correctness.** The hashed-embedding + cosine path is over-engineered for the corpus size (P1-2), but it is *correct* given the inputs. Don't rewrite the retrieval algorithm in this fix cycle — only feature-flag it.
- **`agent/memory/curated/facts.md` empty content.** The curation flow exists (`facts.py` + `aiswmm memory promote-facts`); nothing to fix until users actually staged facts.
- **Test coverage of `_refresh_moc_after_session`.** `tests/test_moc_session_end_failure_swallowed.py` and `tests/test_moc_auto_refresh_on_session_end.py` already exercise the happy + failure paths.
- **README marketing copy.** Not in scope.
