# Agentic SWMM — NL→SWMM evidence (LLM-engaged path, gpt-5.5-2026-04-23)

This run is the **LLM-engaged** companion to the deterministic baseline captured in
`docs/agent-nl-swmm-gpt55-evidence-20260515.md`. The agent's deterministic
auto-router was disabled via `AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER=1`, so every
tool was selected by `gpt-5.5-2026-04-23` over the OpenAI Responses API. The
purpose is paper-figure source material: hard proof that the natural-language →
SWMM pipeline works when the LLM is actually doing the planning.

## 1. Configuration

| field | value |
|---|---|
| commit | `2b99264d3fb2257097c8d513e8ec547647625edf` (working tree had uncommitted edits to `agentic_swmm/agent/runtime_loop.py`, `tool_registry.py`, `docs/*.md`; no source edits made during this run) |
| planner | `openai` (real Responses API, no mock) |
| model (pinned) | `gpt-5.5-2026-04-23` |
| `~/.aiswmm/config.toml` model | `gpt-5.5-2026-04-23` |
| `AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER` | `1` (confirmed against `agentic_swmm/agent/planner.py:112`) |
| `AISWMM_OPENAI_MOCK_RESPONSE` | unset |
| max-steps | `10` |
| session dir | `runs/agent/agent-1778818227/` |
| wall-clock | 32 s |
| start (UTC) | 2026-05-15 04:10:28 |

Command (sanitized — secrets loaded from `~/.aiswmm/env`):

```bash
set -a && . ~/.aiswmm/env && set +a && \
unset AISWMM_OPENAI_MOCK_RESPONSE && \
export AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER=1 && \
python3.11 -m agentic_swmm.cli agent \
  --planner openai \
  --model gpt-5.5-2026-04-23 \
  --max-steps 10 \
  "Run examples/tecnopolo/tecnopolo_r1_199401.inp through SWMM and report the peak inflow at the outlet. Then audit the run."
```

## 2. Natural-language prompt (verbatim — same as deterministic baseline)

> Run examples/tecnopolo/tecnopolo_r1_199401.inp through SWMM and report the peak inflow at the outlet. Then audit the run.

## 3. LLM activity proof — critical evidence

Source: `runs/agent/agent-1778818227/09_audit/llm_calls.jsonl`
(10 records; SHA-256 prefix `3dd00d71d5db`).

| call | step | tokens_input | tokens_output | response_id (last 8) | planner latency |
|---:|---|---:|---:|---|---:|
| 1 | list_skills | 4,196 | 127 | `…007b6a61` | 3,956 ms |
| 2 | select_workflow_mode | 5,192 | 150 | `…2b5514c2` | 3,666 ms |
| 3 | search_files `[OUTFALLS]` | 6,004 | 124 | `…150ce88d` | 3,294 ms |
| 4 | read_file (.inp) | 6,468 | 35 | `…eed97e01` | 2,033 ms |
| 5 | search_files `OUT_0` | 8,100 | 67 | `…c62e93e9` | 3,035 ms |
| 6 | search_files `OU2` | 9,927 | 45 | `…77286d66` | 2,098 ms |
| 7 | select_skill swmm-runner | 11,251 | 44 | `…37354333` | 3,251 ms |
| 8 | run_swmm_inp | 11,477 | 88 | `…4b15a2a8` | 3,091 ms |
| 9 | select_skill swmm-experiment-audit | 13,749 | 47 | `…a4c4be20` | 2,762 ms |
| 10 | audit_run | 13,966 | 94 | `…7329a878` | 2,334 ms |
| **total** |  | **90,330** | **821** | 10/10 non-null | sum 29,520 ms |

Every record has:
- non-empty `prompt_full_ref` pointing at a per-call file under `09_audit/llm_prompts/` (10 files, 10–16 KB each)
- non-empty `response_text` containing the full OpenAI Responses payload with `id: resp_…`, `status: completed`, and `tool_calls` arrays
- `model_version = gpt-5.5-2026-04-23` and `caller = planner`
- non-null `duration_ms`

This is incompatible with the deterministic auto-router being engaged: the router emits no `llm_calls.jsonl` rows at all because it never calls the LLM.

## 4. Tool trace (paper figure candidate)

Source: pairing `tool_start` and `tool_result` events in `runs/agent/agent-1778818227/agent_trace.jsonl`, cross-referenced with `09_audit/llm_calls.jsonl` durations.

| # | tool | args (truncated) | status | planner latency (ms) |
|---:|---|---|---|---:|
| 1 | `list_skills` | — | ok (14 skills) | 3,956 |
| 2 | `select_workflow_mode` | goal=…, inp_path=examples/tecnopolo/tecnopolo_r1_199401.inp | ok (mode=`prepared_inp_cli`, missing=0) | 3,666 |
| 3 | `search_files` | query=`[OUTFALLS]`, glob=tecnopolo_r1_199401.inp, max=5 | ok (5 matches) | 3,294 |
| 4 | `read_file` | path=examples/tecnopolo/tecnopolo_r1_199401.inp | ok | 2,033 |
| 5 | `search_files` | query=`OUT_0`, glob=tecnopolo_r1_199401.inp, max=20 | ok (20 matches) | 3,035 |
| 6 | `search_files` | query=`OU2`, glob=tecnopolo_r1_199401.inp, max=20 | ok (20 matches) | 2,098 |
| 7 | `select_skill` | skill_name=`swmm-runner` | ok | 3,251 |
| 8 | `run_swmm_inp` | inp_path=…tecnopolo_r1_199401.inp, node=`OU2`, run_dir=session | ok (swmm-runner.swmm_run completed) | 3,091 |
| 9 | `select_skill` | skill_name=`swmm-experiment-audit` | ok | 2,762 |
| 10 | `audit_run` | run_dir=session, workflow_mode=prepared_inp_cli | **fail** (`MCP transport failed: MCP process ended before sending a complete line.`) | 2,334 |

9 / 10 succeeded. The failing call is the same audit MCP transport issue
observed yesterday with `gpt-4o-mini` — not a model-side regression. The
audit was completed via CLI fallback (see §6).

## 5. SWMM result

Source: `runs/agent/agent-1778818227/model.rpt` (Outfall Loading Summary, lines copied verbatim):

```
  Outfall Loading Summary
  ***********************
  -----------------------------------------------------------
                         Flow       Avg       Max       Total
                         Freq      Flow      Flow      Volume
  Outfall Node           Pcnt       CMS       CMS    10^6 ltr
  -----------------------------------------------------------
  OU2                   32.71     0.004     0.061       0.484
  OUT_0                 34.04     0.004     0.061       0.489
  -----------------------------------------------------------
  System                33.38     0.007     0.122       0.973
```

| metric | LLM-engaged | deterministic baseline (yesterday) | match? |
|---|---:|---:|---|
| Peak inflow @ OUT_0 | 0.061 CMS | 0.061 CMS | yes (exact) |
| Peak inflow @ OU2  | 0.061 CMS | (not reported in baseline; LLM chose this outlet) | n/a |
| Runoff continuity error | -0.130 % | -0.130 % | yes (exact) |
| Flow routing continuity error | -0.004 % | -0.004 % | yes (exact) |
| Return code | 0 | 0 | yes |

The LLM observed two outfalls (OUT_0 and OU2) and picked `OU2` to pass to the
runner's `node` argument. Both outlets register an identical peak (0.061 CMS),
so the deterministic-baseline match is exact.

## 6. Audit artifacts

Initial agent-issued `audit_run` failed with an MCP transport error
(`MCP process ended before sending a complete line.`). Per the runbook,
`aiswmm audit` CLI was used as fallback and reported `status: pass`.

`ls -la runs/agent/agent-1778818227/09_audit/`:

| file | size (B) | SHA-256 prefix |
|---|---:|---|
| `llm_calls.jsonl` | 47,248 | `3dd00d71d5db` |
| `experiment_provenance.json` | 8,472 | `4ee83d73a95a` |
| `experiment_note.md` | 2,979 | `909236635255` |
| `model_diagnostics.json` | 285 | `f361bb5f073c` |
| `comparison.json` | 255 | `4951e3000e38` |
| `llm_prompts/` (dir) | — | 10 prompt files, 10,318–16,041 B each |

Also at the session root: `model.rpt` (SHA-256 prefix `23d7ebcc180f`, 23,776 B).

CLI fallback command:

```bash
python3.11 -m agentic_swmm.cli audit \
  --run-dir runs/agent/agent-1778818227 \
  --workflow-mode prepared_inp_cli \
  --objective "Run examples/tecnopolo/tecnopolo_r1_199401.inp through SWMM and report peak inflow at outlet; audit run"
```

Returned `{"ok": true, "status": "pass", "memory_hook.skipped": true (run path matches runs/agent/agent-*/)}`.

## 7. Memory absorption

Pre / post snapshots written via `skills/swmm-modeling-memory/scripts/summarize_memory.py`:

| metric | pre (`_test_pre_llm_20260515T041013Z`) | post (`_test_post_llm_20260515T041233Z`) | delta |
|---|---:|---:|---:|
| run folders scanned | 17 | 18 | +1 |
| audit records found | 17 | 18 | +1 |
| runs with detected failures | 10 | 11 | +1 |
| project memory groups | 10 | 11 | +1 (new bucket `agent-1778818227`) |

`modeling_memory_index.md` diff adds exactly one row for `agent-1778818227`:

```
| agent-1778818227 | agent-1778818227 | agent-1778818227 | prepared_inp_cli | pass |  | not_requested |  | continuity_parse_missing, missing_inp, partial_run, peak_flow_parse_missing |  |  |
```

The "detected failures" tags (`continuity_parse_missing`, `missing_inp`,
`peak_flow_parse_missing`, `partial_run`) reflect the summariser's heuristic
read of the session dir layout — the run itself passes SWMM continuity. This
is the same labelling pattern the deterministic baseline produced.

## 8. Cost

OpenAI public pricing for `gpt-5.5-2026-04-23` is not yet documented in this
repo; estimating against `gpt-5`-class rate bands using the run's measured
usage (90,330 input + 821 output tokens):

| band | input rate | output rate | USD |
|---|---:|---:|---:|
| low | $5 / Mtok | $15 / Mtok | $0.46 |
| mid | $10 / Mtok | $30 / Mtok | $0.93 |
| high | $15 / Mtok | $60 / Mtok | $1.40 |

All bands sit well under the $3.00 hard halt. The agent's run logs do not
surface a USD figure directly.

## 9. UX observations

Tool plan was sensible:
1. Discover skills (`list_skills`).
2. Decide workflow mode (`select_workflow_mode` → `prepared_inp_cli`).
3. Locate outlet node before running (search_files × 3, read_file × 1).
   - The LLM read the `[OUTFALLS]` section, saw two candidates (OUT_0, OU2),
     and grepped each by name to make sure it picked a real node. This is
     more diligence than the deterministic router uses, which simply hard-codes
     a default. It cost 4 extra tool calls but produced a defensible
     `node=OU2` argument.
4. Commit to `swmm-runner` skill, then run SWMM (`run_swmm_inp`).
5. Commit to `swmm-experiment-audit` skill, then attempt `audit_run` (failed
   on MCP transport — not the LLM's fault).

No clarifying questions, no flailing, no off-task tool calls. The four
discovery/grep calls (#3 #4 #5 #6) are arguably one or two more than strictly
needed, but each is cheap (10 KB prompt, < 100 output tokens) and each one
narrowed an honest ambiguity.

## 10. Reproducibility

Pre-snapshot:

```bash
python3.13 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs \
  --out-dir memory/_test_pre_llm_$(date -u +%Y%m%dT%H%M%SZ) \
  --no-run-summaries
```

Agent run:

```bash
set -a && . ~/.aiswmm/env && set +a && \
unset AISWMM_OPENAI_MOCK_RESPONSE && \
export AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER=1 && \
python3.11 -m agentic_swmm.cli agent \
  --planner openai \
  --model gpt-5.5-2026-04-23 \
  --max-steps 10 \
  "Run examples/tecnopolo/tecnopolo_r1_199401.inp through SWMM and report the peak inflow at the outlet. Then audit the run."
```

Audit CLI fallback:

```bash
python3.11 -m agentic_swmm.cli audit \
  --run-dir runs/agent/agent-1778818227 \
  --workflow-mode prepared_inp_cli \
  --objective "Run examples/tecnopolo/tecnopolo_r1_199401.inp through SWMM and report peak inflow at outlet; audit run"
```

Post-snapshot:

```bash
python3.13 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs \
  --out-dir memory/_test_post_llm_$(date -u +%Y%m%dT%H%M%SZ) \
  --no-run-summaries
```
