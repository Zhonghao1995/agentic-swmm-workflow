# Agent NL -> SWMM End-to-End Evidence with gpt-5.5 (2026-05-15)

This document is the paper-grade evidence record for a single, bounded
end-to-end run of the `aiswmm` agent against the `gpt-5.5-2026-04-23`
planner. It supplements `docs/agent-nl-swmm-evidence-20260514.md`
(which used `gpt-4o-mini`) and is intended to be the primary source for
manuscript Figures 4-7 (tool trace, run metrics, audit artifacts,
memory absorption).

## Test configuration

| Field | Value |
| --- | --- |
| Repo HEAD | `2b99264d3fb2257097c8d513e8ec547647625edf` |
| Branch | `main` |
| Python | 3.11.14 (CLI) + 3.13.11 (memory summariser) |
| `aiswmm` CLI version | `agentic-swmm 0.6.1` |
| Planner provider | `openai` |
| Planner model (pinned snapshot) | `gpt-5.5-2026-04-23` |
| Max steps | 8 |
| SWMM engine | EPA SWMM 5.2 (Build 5.2.4) |
| Session directory | `runs/agent/agent-1778817623` |
| Input file | `examples/tecnopolo/tecnopolo_r1_199401.inp` (sha256 `48445eec9c5d99...`) |
| MCP registry | `~/.aiswmm/mcp.json`, 11 servers (all 11 on-disk MCPs registered) |

## Natural-language prompt (verbatim)

> Run examples/tecnopolo/tecnopolo_r1_199401.inp through SWMM and report the peak inflow at the outlet. Then audit the run.

One English sentence, no clarifying questions asked back. Agent ingested
it as `goal`, routed it through the workflow router, and produced
artifacts without further user interaction.

## Tool trace (paper Figure candidate #1)

Reconstructed from `runs/agent/agent-1778817623/agent_trace.jsonl` and
the CLI tee log (`/tmp/aiswmm_gpt55_run.log`).

| #  | Tool                  | Server / module         | Status | Note                                                                 |
|----|-----------------------|-------------------------|--------|----------------------------------------------------------------------|
| 1  | `list_skills`         | local (registry)        | ok     | 14 skills available                                                  |
| 2  | `read_skill`          | local (registry)        | ok     | read `swmm-end-to-end`                                               |
| 3  | `read_skill`          | local (registry)        | ok     | read `swmm-runner`                                                   |
| 4  | `read_skill`          | local (registry)        | ok     | read `swmm-experiment-audit`                                         |
| 5  | `list_mcp_servers`    | local (registry)        | ok     | 11 configured MCP server(s)                                          |
| 6  | `list_mcp_tools`      | `swmm-runner`           | ok     | 4 MCP tools / 4 schemas cached                                       |
| 7  | `select_workflow_mode`| local router            | ok     | mode=`prepared_inp_cli`, missing inputs=0                            |
| 8  | `run_swmm_inp`        | `swmm-runner` (MCP)     | ok     | EPA SWMM 5.2.4 ran successfully (`stdout.txt` ~20 KB, RPT 23 KB)     |
| 9  | `audit_run`           | `swmm-experiment-audit` | FAIL   | `MCP transport failed: MCP process ended before sending a complete line.` |
| 9' | `aiswmm audit` (CLI fallback) | local             | ok     | Deterministic audit produced 4 artifacts in `09_audit/`, status=pass |

### Important finding: deterministic auto-router short-circuited the LLM

`agentic_swmm/agent/planner.py:131-169` detects "looks like SWMM
request" goals and routes them through `_run_prepared_inp_workflow`
*without* calling the OpenAI Responses API. Consequently this run did
**not** consume any `gpt-5.5-2026-04-23` planner tokens for steps 1-9.
Evidence:

- No `09_audit/llm_calls.jsonl` was emitted before the CLI-audit
  fallback (the planner's `record_llm_call` was never reached).
- `agent_trace.jsonl` contains zero `planner_response` events
  (`Counter({'tool_start': 9, 'tool_result': 9, 'session_start': 1,
  'session_state': 1, 'session_end': 1})`).
- Total wall clock from `session_start` to `session_end` was within a
  single one-second tick (`2026-05-15T04:00:23+00:00` ->
  `2026-05-15T04:00:24+00:00`), which is consistent with deterministic
  Python execution but not with 9 round-trips to OpenAI.

This is a model-agnostic, deterministic success path. For the paper,
the implication is: *the agent system handles natural-language SWMM
runs reliably, but for prepared-INP CLI requests the LLM is not on the
critical path. A test that requires LLM token expenditure must use a
prompt outside `_looks_like_swmm_request` or set
`AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER=1`.*

## SWMM result (paper Figure candidate #2)

Parsed from `runs/agent/agent-1778817623/model.rpt` and `manifest.json`.

| Metric | Value | Source |
| --- | --- | --- |
| Engine | EPA SWMM 5.2 (Build 5.2.4) | `manifest.json -> swmm5.version` |
| Simulation status | "Simulation complete" / "EPA SWMM completed in 1.00 seconds." | `stdout.txt` |
| Return code | 0 (no `stderr.txt` content) | `stderr.txt` (0 bytes) |
| Peak inflow at `OU2` | 0.061 CMS | RPT "Outfall Loading Summary" |
| Peak inflow at `OUT_0` | 0.061 CMS | RPT "Outfall Loading Summary" |
| System peak | 0.122 CMS | RPT "Outfall Loading Summary" |
| Peak from manifest metrics | node=`OU2`, peak=0.061 CMS, t=03:15 | `manifest.json -> metrics.peak` |
| Runoff Quantity continuity error | -0.130 % | RPT |
| Flow Routing continuity error   | -0.004 % | RPT |

### Baseline match check vs `docs/agent-nl-swmm-evidence-20260514.md`

The 2026-05-14 baseline run (gpt-4o-mini, same INP) reported a peak
inflow of **0.061 CMS at `OUT_0`**. This run reports **0.061 CMS at
`OUT_0`**. Exact match -> agreement to the precision SWMM reports
(three significant figures). Continuity error magnitudes are also
within reasonable bounds (well under the 1% rule-of-thumb threshold).

## Audit artifacts produced (paper Figure candidate #3)

Listing of `runs/agent/agent-1778817623/09_audit/`:

```
-rw-r--r--  1 zhonghao  staff   255 May 14 21:01 comparison.json
-rw-r--r--  1 zhonghao  staff  2979 May 14 21:01 experiment_note.md
-rw-r--r--  1 zhonghao  staff  8286 May 14 21:01 experiment_provenance.json
-rw-r--r--  1 zhonghao  staff   285 May 14 21:01 model_diagnostics.json
```

| File | Size (B) | SHA-256 (first 12) |
| --- | ---: | --- |
| `comparison.json`            | 255  | `e6d010549b98` |
| `experiment_note.md`         | 2979 | `c5eff7a9148b` |
| `experiment_provenance.json` | 8286 | `88a2265a0a5b` |
| `model_diagnostics.json`     | 285  | `1edbf4117ba9` |

`model_diagnostics.json` excerpt:

```json
{
  "diagnostics": [],
  "error_count": 0,
  "generated_at_utc": "2026-05-15T04:01:53+00:00",
  "generated_by": "swmm-experiment-audit",
  "schema_version": "1.1",
  "source_inp": null,
  "source_rpt": "runs/agent/agent-1778817623/model.rpt",
  "status": "pass",
  "warning_count": 0
}
```

Notes:

- Audit `status = pass`, 0 errors, 0 warnings.
- These artifacts were produced by the CLI fallback (`aiswmm audit`),
  not by the agent's step-9 `audit_run` tool call. The reason is
  recorded in step 9 of the trace and reproduced in the next section.
- The audit's `memory_hook` step intentionally **skipped**:
  ```json
  {"skipped": true, "reason": "run path matches runs/agent/agent-*/"}
  ```
  This is by design (agent sessions feed memory via the summariser
  rather than the audit hook).

### Step-9 MCP failure (verbatim from CLI)

```
[9] audit_run
[/] Running audit_run - Audit a run directory and write deterministic provenance/comparison/note artifacts
FAILED: MCP transport failed: MCP process ended before sending a complete line.
Final report: runs/agent/agent-1778817623/final_report.md
SWMM ran, but audit generation failed; inspect the saved audit tool artifacts.
```

This is the same `swmm-experiment-audit` MCP transport flake that the
2026-05-14 evidence doc also worked around with `aiswmm audit`.
Worth raising as an open issue separately. The CLI fallback is fully
deterministic and produced identical-quality artifacts.

## Memory layer before/after (paper Figure candidate #4)

Both snapshots produced by
`skills/swmm-modeling-memory/scripts/summarize_memory.py --runs-dir runs
--no-run-summaries`.

| Metric | Pre (`memory/_test_pre_20260515T040004Z/`) | Post (`memory/_test_post_20260515T040208Z/`) |
| --- | ---: | ---: |
| `run_folder_count`        | 16 | **17** |
| `record_count`            | 16 | **17** |
| `failure_record_count`    | 9  | **10** |
| Projects tracked          | 9  | **10** |

- **New project bucket absorbed:** `agent-gpt55-demo` (1 record). This
  matches the `--case-name "agent-gpt55-demo"` argument passed to
  `aiswmm audit`. Confirmed via `project_counts` delta:

  ```
  agent-gpt55-demo : 0 -> 1   NEW
  ```

- **`runs_discovered` incremented:** 16 -> 17 (confirmed
  `run_folder_count` and `record_count` both advanced by exactly one).

- **Failure-pattern table delta** (count is records flagged with that
  pattern, not severity):

  | Pattern                    | Pre | Post | Delta |
  | --- | ---: | ---: | ---: |
  | `continuity_parse_missing` | 9   | 10   | +1    |
  | `missing_inp`              | 3   | 4    | +1    |
  | `missing_manifest`         | 3   | 3    | 0     |
  | `no_detected_failure`      | 7   | 7    | 0     |
  | `partial_run`              | 9   | 10   | +1    |
  | `peak_flow_parse_missing`  | 6   | 7    | +1    |

  All four +1 deltas point at the same new record (`agent-1778817623`):

  ```
  run_id: agent-1778817623
  failure_patterns:
    - continuity_parse_missing
    - missing_inp
    - partial_run
    - peak_flow_parse_missing
  ```

  **Important caveat for the manuscript:** these flags are
  *false positives* from the memory summariser. The actual run has a
  complete `model.rpt` with both continuity errors (-0.130 % and
  -0.004 %), the peak inflow (0.061 CMS), and a `manifest.json`
  pointing at the INP path. The summariser is looking for these fields
  in a different folder layout (`runs/<project>/<run_id>/...`) than
  what agent sessions produce (`runs/agent/agent-<id>/...`). So:
  *the memory layer registered the new run but failed to extract its
  metrics.* This is a concrete UX/integration nit (see below).

### Bottom line: memory absorption

Yes, the post-test memory snapshot contains the new run. The bucket
appeared, the count incremented, and the run was indexed - but the
metric-level extraction (peak flow, continuity) did not succeed because
of the folder-layout mismatch.

## Cost & timing

| Quantity | Value | Source |
| --- | --- | --- |
| Wall-clock (session_start -> session_end) | ~1 s (within a single 1-second timestamp tick) | `agent_trace.jsonl` |
| SWMM solver wall-clock | 1.00 s | `stdout.txt` "EPA SWMM completed in 1.00 seconds." |
| Tool calls completed | 8 of 9 planned | `final_report.md` "Metrics: 8/9 tool calls succeeded" |
| Planner LLM calls made | **0** (auto-router short-circuited the LLM) | absence of `planner_response` events in `agent_trace.jsonl` |
| Approximate OpenAI token usage / cost | $0.00 surfaced (no LLM calls in this path) | not applicable |

**Caveat.** The `gpt-5.5-2026-04-23` snapshot was loaded by the planner
(visible in `session_state.json`) but was never invoked because the
auto-workflow router intercepted the goal. For an LLM-driven trace
under this model, a follow-up run must either (a) use a prompt the
router does not classify as a SWMM request, or (b) set
`AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER=1`. Recommended for a separate
test, not for this paper-grade evidence pass.

## User-friendliness assessment

### What worked

- **One English sentence sufficed.** No clarifying back-and-forth. The
  router picked up the INP path verbatim from the prompt, selected
  `prepared_inp_cli`, and executed.
- **Clear final status panel.** The boxed "RUN COMPLETE" summary at the
  end of `stdout` shows outcome, run dir, tool count, artifact count,
  evidence boundary, and next-action pointer in one screen. A
  non-expert can read it.
- **Audit fallback path is documented inline.** When step 9 failed,
  the agent's final answer told the user the run succeeded *and where
  to look*: "SWMM ran, but audit generation failed; inspect the saved
  audit tool artifacts."

### What confused / nits to fix

- **UX nit #1: outcome label is misleading.** The boxed summary says
  `Outcome: FAIL` purely because step 9 (audit) errored, even though
  SWMM itself succeeded and the user's stated goal ("Run ... and report
  the peak inflow") was fully achieved on disk. A non-expert reading
  the panel would conclude the run was broken. Suggested change: split
  the outcome into "primary goal" and "auxiliary steps" - or downgrade
  audit-only failures to a `WARN` outcome. Pointer:
  `agentic_swmm/agent/planner.py` (the `ok = False` setting near
  giveup_tool / step 9 failure) and `agentic_swmm/cli/...`-side
  rendering of the panel.

- **UX nit #2: the planner model name in the banner promises something
  that does not happen for this kind of prompt.** The CLI prints
  `Planner: openai (gpt-5.5-2026-04-23)` and the user reasonably
  assumes the model is making decisions. For prepared-INP CLI goals
  the auto-router runs deterministic Python and the LLM is never
  called. Suggested change: emit a one-line trace event such as
  `auto_workflow_router: prepared_inp_cli (LLM bypassed)` to make the
  deterministic shortcut visible. Pointer:
  `agentic_swmm/agent/planner.py:131` (entry into `_consult_workflow_skills`
  / `select_workflow_mode`).

- **UX nit #3 (memory layer).** The post-test memory diff flagged
  four "failure patterns" on a clean run because the summariser
  expects `runs/<project>/<run_id>/...` but the agent writes to
  `runs/agent/agent-<id>/...`. The flags will confuse anyone reading
  `lessons_learned.md`. Pointer:
  `skills/swmm-modeling-memory/scripts/summarize_memory.py` (the
  metric-parsing path; would need to teach it the `runs/agent/agent-*`
  layout or wire it through `manifest.json`).

## Reproducibility

Exact commands used in this evidence pass, ready to copy-paste from the
repo root (`/Users/zhonghao/Desktop/Codex Project/Agentic SWMM`):

```bash
# Step 1 - pre-test memory snapshot
PRE_TS=$(date -u +%Y%m%dT%H%M%SZ)
python3.13 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs \
  --out-dir "memory/_test_pre_${PRE_TS}" \
  --no-run-summaries

# Step 2 - the one bounded agent run
set -a && . ~/.aiswmm/env && set +a
unset AISWMM_OPENAI_MOCK_RESPONSE
python3.11 -m agentic_swmm.cli agent \
  --planner openai \
  --model gpt-5.5-2026-04-23 \
  --max-steps 8 \
  "Run examples/tecnopolo/tecnopolo_r1_199401.inp through SWMM and report the peak inflow at the outlet. Then audit the run." \
  2>&1 | tee /tmp/aiswmm_gpt55_run.log

# Step 2b - CLI audit fallback (because step 9 of the agent failed)
python3.11 -m agentic_swmm.cli audit \
  --run-dir runs/agent/agent-1778817623 \
  --workflow-mode prepared_inp_cli \
  --case-name "agent-gpt55-demo" \
  --objective "Validate NL->SWMM path with gpt-5.5-2026-04-23"

# Step 3 - post-test memory snapshot
POST_TS=$(date -u +%Y%m%dT%H%M%SZ)
python3.13 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs \
  --out-dir "memory/_test_post_${POST_TS}" \
  --no-run-summaries
```

## File pointers (all absolute)

- Session: `/Users/zhonghao/Desktop/Codex Project/Agentic SWMM/runs/agent/agent-1778817623/`
- Trace: `/Users/zhonghao/Desktop/Codex Project/Agentic SWMM/runs/agent/agent-1778817623/agent_trace.jsonl`
- SWMM report: `/Users/zhonghao/Desktop/Codex Project/Agentic SWMM/runs/agent/agent-1778817623/model.rpt`
- Manifest: `/Users/zhonghao/Desktop/Codex Project/Agentic SWMM/runs/agent/agent-1778817623/manifest.json`
- Audit folder: `/Users/zhonghao/Desktop/Codex Project/Agentic SWMM/runs/agent/agent-1778817623/09_audit/`
- Pre-snapshot: `/Users/zhonghao/Desktop/Codex Project/Agentic SWMM/memory/_test_pre_20260515T040004Z/`
- Post-snapshot: `/Users/zhonghao/Desktop/Codex Project/Agentic SWMM/memory/_test_post_20260515T040208Z/`
- Baseline (gpt-4o-mini): `/Users/zhonghao/Desktop/Codex Project/Agentic SWMM/docs/agent-nl-swmm-evidence-20260514.md`
