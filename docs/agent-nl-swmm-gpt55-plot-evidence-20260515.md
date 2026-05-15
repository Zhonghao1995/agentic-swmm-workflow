# NL -> SWMM + Plot E2E Evidence (gpt-5.5, auto-router disabled) - 2026-05-15

Companion to `docs/agent-nl-swmm-gpt55-llm-engaged-evidence-20260515.md`. The
prior run proved the LLM plans SWMM execution + audit on its own. This run
asks the open question that matters for the manuscript: **does the LLM
autonomously call the `swmm-plot` skill when the natural-language prompt asks
for a figure?**

## 1. Configuration

| Field | Value |
| --- | --- |
| Repository commit | `2b99264d3fb2257097c8d513e8ec547647625edf` (`main`) |
| Working tree | Dirty (uncommitted edits in `agentic_swmm/agent/runtime_loop.py`, `agentic_swmm/agent/tool_registry.py`, two unrelated docs) |
| Planner | `openai` |
| Pinned model | `gpt-5.5-2026-04-23` (configured in `~/.aiswmm/config.toml`) |
| Max steps | 14 |
| Auto-router gate | `AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER=1` (asserts `planner.py:131`) |
| `AISWMM_OPENAI_MOCK_RESPONSE` | unset |
| API key | sourced from `~/.aiswmm/env`, never echoed |
| Input | `examples/tecnopolo/tecnopolo_r1_199401.inp` |
| SWMM binary | `swmm5` v5.2.4 |
| Session folder | `runs/agent/agent-1778819330/` |

## 2. Natural-language prompt (verbatim)

> Run examples/tecnopolo/tecnopolo_r1_199401.inp through SWMM, report the peak
> inflow at the outlet, plot the rainfall-runoff hydrograph at the outlet as a
> PNG, and audit the run.

## 3. LLM activity proof

Source: `runs/agent/agent-1778819330/09_audit/llm_calls.jsonl` (9 calls).
Aggregated via the audit-pipeline schema (`tokens_input` / `tokens_output`).

| call | step | tokens_input | tokens_output | tool_calls_emitted | duration (ms) | call_id (last 8) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | 4 210 | 206 | 2 | 4 295 | `10c7a796` |
| 2 | 2 | 5 556 | 250 | 2 | 3 560 | `f32a81d2` |
| 3 | 3 | 6 830 | 71  | 1 | 2 451 | `23b39dff` |
| 4 | 4 | 8 498 | 119 | 1 | 3 020 | `475158b3` |
| 5 | 5 | 10 376| 67  | 1 | 2 528 | `375942b8` |
| 6 | 6 | 11 110| 40  | 1 | 2 707 | `17d8c75a` |
| 7 | 7 | 11 332| 88  | 1 | 2 106 | `6dc4521a` |
| 8 | 8 | 13 604| 206 | 1 | 4 892 | `6ad33f00` |
| 9 | 9 | 13 980| 218 | 2 | 4 205 | `604b36da` |
| **total** | | **85 496** | **1 265** | **12** | **29 764 ms** | |

`model_alias` and `model_version` are `gpt-5.5-2026-04-23` on every entry.
Sample planner-response IDs (last 8) from the live agent trace (decisive that
real OpenAI API calls happened, not the mock): `beccaca8`, `3c4ec3ca`,
`69b7a1f4`, `a76fa5f6`, `7226338e`, `d9b3a0da`, `a34772c4`, `8690a772`,
`2e0addf2`.

## 4. Tool trace

Decoded from `runs/agent/agent-1778819330/agent_trace.jsonl`. The "planner step"
column is the LLM round in which each tool was *batched*; multiple tools per
batch indicates planner-issued parallelism.

| planner step | tool index | tool | result summary | notes |
| --- | --- | --- | --- | --- |
| 1 | 1 | `list_skills` | 14 skills | discovery |
| 1 | 2 | `list_dir` | 5 entries | discovery |
| 2 | 3 | `select_workflow_mode` | `mode=prepared_inp_cli missing=0` | autonomous mode pick |
| 2 | 4 | `search_files` | 5 matches | locate `.inp` |
| 3 | 5 | `read_file` | reads `examples/tecnopolo/tecnopolo_r1_199401.inp` | scan outfalls |
| 4 | 6 | `search_files` | 20 matches | locate outlet ids |
| 5 | 7 | `search_files` | 10 matches | confirm outlet node |
| 6 | 8 | `select_skill` | committed to `swmm-runner` | autonomous skill pick |
| 7 | 9 | `run_swmm_inp` | ok, calls `swmm-runner.swmm_run` | exact baseline match (see s5) |
| 8 | 10 | `select_skill` | committed to `swmm-experiment-audit` | autonomous skill pick |
| 9 | 11 | **`audit_run`** | **FAILED: MCP transport ended before sending a complete line.** | known regression |
| 9 | (planned) | **`inspect_plot_options`** | **never executed -- batch cancelled when `audit_run` failed and the next round raised HTTP 400 `No tool output found for function call ...`** | **PROOF the LLM autonomously selected the `swmm-plot` skill** |

**Step 9 is the load-bearing finding.** The planner emitted a *parallel* batch
of two tool calls in the same response (`response_id` ...`2e0addf2`):

```jsonc
// tool 1 (audit)
{ "tool": "audit_run",            "call_id": "call_Q2aawIyxmxcLjAlhASatcaoM",
  "args": { "objective": "... plot rainfall-runoff hydrograph at the outlet, and audit the run.",
            "run_dir": ".../runs/agent/agent-1778819330",
            "workflow_mode": "prepared_inp_cli" } }
// tool 2 (plot - autonomously selected by the LLM)
{ "tool": "inspect_plot_options", "call_id": "call_wjqCKrxsprWkZZEm81U9LhCV",
  "args": { "inp_path": "examples/tecnopolo/tecnopolo_r1_199401.inp",
            "out_file": ".../runs/agent/agent-1778819330/plot_options.json",
            "run_dir":  ".../runs/agent/agent-1778819330" } }
```

`inspect_plot_options` is a tool exposed by the **`swmm-plot`** skill. The LLM
had not previously executed `select_skill("swmm-plot")`, yet it knew that
honoring the user's PNG request required engaging the plotting capability and
queued the discovery call. The transport bug aborted the batch before the
agent could render -- so no PNG was produced, but the **intent and tool
selection are recorded in writing in the trace**.

## 5. SWMM result vs 0.061 CMS baseline

`runs/agent/agent-1778819330/manifest.json` peak metric:

```json
{ "peak": { "node": "OU2", "peak": 0.061, "time_hhmm": "03:15",
            "source": "Node Inflow Summary" } }
```

Continuity errors from `model.rpt`:

| Mass balance | Value | Yesterday's baseline | Match |
| --- | --- | --- | --- |
| Runoff quantity continuity error | -0.130 % | -0.130 % | exact |
| Flow routing continuity error    | -0.004 % | -0.004 % | exact |
| Peak inflow @ OU2 / OUT_0       | 0.061 CMS @ day 10 03:15 | 0.061 CMS | exact |

SWMM execution is fully deterministic against the documented baseline.

## 6. Generated figure

**No PNG was produced in this run.** The figure pipeline aborted at the
`audit_run` MCP transport failure (step 11) before `inspect_plot_options`
could resolve and before any `swmm-plot.render_*` tool was reached.

For the paper, the manuscript-quality reference figure remains the previously
generated:

- absolute path: `/Users/zhonghao/Desktop/Codex Project/Agentic SWMM/runs/end-to-end/tecnopolo-199401-prepared/08_plot/rain_runoff_mcp.png`
- file size: 93 082 bytes (90.9 KiB)
- pixel dimensions: 2700 x 1140 px (PNG, 24-bit)
- empty/zero-byte? No.

Embed for the paper draft (resolved relative to repo root):

```markdown
![Tecnopolo 1994-01 rainfall-runoff hydrograph at the outlet (reference render
from earlier deterministic E2E run, not produced by this agent session)](../runs/end-to-end/tecnopolo-199401-prepared/08_plot/rain_runoff_mcp.png)
```

If the manuscript needs an agent-generated PNG, the right next step is **not**
another agent run -- it is fixing the `audit_run` MCP transport bug so the
parallel batch at step 9 completes. The LLM has already demonstrated it will
pick the right tool.

## 7. Audit artifacts produced

`audit_run` (MCP path) failed inside the agent. The documented fallback --
`aiswmm audit` CLI -- was used. Result:

```
{ "ok": true, "run_id": "agent-1778819330", "status": "pass",
  "experiment_provenance":  "runs/agent/agent-1778819330/09_audit/experiment_provenance.json",
  "comparison":             "runs/agent/agent-1778819330/09_audit/comparison.json",
  "experiment_note":        "runs/agent/agent-1778819330/09_audit/experiment_note.md",
  "model_diagnostics":      "runs/agent/agent-1778819330/09_audit/model_diagnostics.json" }
```

Provenance, model diagnostics, experiment note, and comparison stub all
written. No obsidian export and no `--compare-to` was requested.

## 8. Memory absorption

| Snapshot | record_count | run_folder_count | project_buckets |
| --- | --- | --- | --- |
| pre  (`memory/_test_pre_plot_20260515T042833Z/`)  | 18 | 18 | 11 |
| post (`memory/_test_post_plot_20260515T043057Z/`) | 19 | 19 | 12 |

New project bucket: **`agent-gpt55-plot-demo`** (matches the `--case-name`
passed to the audit fallback). All other buckets stable; `tecnopolo` still
holds 6 records.

## 9. Cost and wall-clock

- Wall-clock from `agent_trace.jsonl` first to last event: **33 s** (then the
  audit fallback added a few additional seconds outside the agent loop).
- Token usage: **85 496 input + 1 265 output = 86 761 total tokens** across
  9 planner round-trips.
- OpenAI public pricing for `gpt-5.5-2026-04-23` is not yet documented in this
  environment; reusing the same upper/middle/lower bands as the 2026-05-14
  evidence file:

| band | input rate | output rate | input USD | output USD | total USD |
| --- | --- | --- | --- | --- | --- |
| low  | $5  / Mtok | $15 / Mtok | $0.4275 | $0.0190 | **$0.45** |
| mid  | $10 / Mtok | $30 / Mtok | $0.8550 | $0.0380 | **$0.89** |
| high | $15 / Mtok | $60 / Mtok | $1.2824 | $0.0759 | **$1.36** |

All bands sit comfortably under the $3.00 hard halt. No retry was issued, so
this is the only chargeable interaction in the session.

## 10. UX observations

**Strength.** Even with auto-routing disabled, `gpt-5.5-2026-04-23` reached
the correct three-tool pipeline (`run_swmm_inp` -> `audit_run` -> the
`swmm-plot` discovery call `inspect_plot_options`) in 9 planner rounds and
30 seconds, with zero hallucinated skills. The fact that it batched
`audit_run` and `inspect_plot_options` in parallel -- without being told the
two are independent -- shows it genuinely understood the workflow rather than
slavishly copying the prompt's word order ("plot ... and audit ...").

**Weakness.** The `audit_run` MCP transport bug (same regression seen in
`runs/agent/agent-1778818227/`) is now blocking a second feature: because it
aborts the parallel batch that also contains the plot tool, the agent has no
opportunity to retry plotting in a later step. From the user's perspective
this looks like "the agent forgot to plot," when in fact it didn't -- the
transport killed the batch mid-flight. The fallback message could surface
this more honestly (e.g. "audit_run failed; queued sibling tool
`inspect_plot_options` was also cancelled -- retry the plot step manually").

## 11. Reproducibility

```bash
# from repo root: /Users/zhonghao/Desktop/Codex Project/Agentic SWMM
git checkout 2b99264d3fb2257097c8d513e8ec547647625edf

# pre snapshot
python3.13 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs --no-run-summaries \
  --out-dir memory/_test_pre_plot_$(date -u +%Y%m%dT%H%M%SZ)

# the run
set -a && . ~/.aiswmm/env && set +a
unset AISWMM_OPENAI_MOCK_RESPONSE
export AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER=1
python3.11 -m agentic_swmm.cli agent \
  --planner openai \
  --model gpt-5.5-2026-04-23 \
  --max-steps 14 \
  "Run examples/tecnopolo/tecnopolo_r1_199401.inp through SWMM, report the peak inflow at the outlet, plot the rainfall-runoff hydrograph at the outlet as a PNG, and audit the run." \
  2>&1 | tee /tmp/aiswmm_gpt55_plot_run.log

# audit fallback (only needed because audit_run hit the MCP transport bug)
python3.11 -m agentic_swmm.cli audit \
  --run-dir runs/agent/agent-1778819330 \
  --workflow-mode prepared_inp_cli \
  --case-name agent-gpt55-plot-demo \
  --objective "Run examples/tecnopolo/tecnopolo_r1_199401.inp through SWMM, report the peak inflow at the outlet, plot the rainfall-runoff hydrograph at the outlet as a PNG, and audit the run."

# post snapshot
python3.13 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs --no-run-summaries \
  --out-dir memory/_test_post_plot_$(date -u +%Y%m%dT%H%M%SZ)
```

## Verdict for the manuscript

Yes -- with auto-routing disabled, `gpt-5.5-2026-04-23` autonomously selected
the `swmm-plot` skill (via `inspect_plot_options`) and batched it in parallel
with the audit step. The transport bug prevented the render call from being
emitted, so no PNG exists from this session; the existing
`runs/end-to-end/tecnopolo-199401-prepared/08_plot/rain_runoff_mcp.png` is the
production-quality figure to embed. The on-disk JSONL trace at
`runs/agent/agent-1778819330/agent_trace.jsonl` (planner response
`...2e0addf2`) is the receipt that the model chose plotting on its own.
