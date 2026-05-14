# Agent NL → SWMM End-to-End Evidence (2026-05-14)

## Purpose

First on-disk verification that the `aiswmm agent` interactive
executor can drive a real SWMM run and audit chain from a single
natural-language prompt. Prior to this test, the only saved chat
sessions under `runs/2026-05-13/*_chat/` and `runs/2026-05-14/*_chat/`
were CI smoke artefacts produced with `AISWMM_OPENAI_MOCK_RESPONSE`
set, which led an external reviewer to conclude the agent layer
was non-functional. This document corrects that record.

## Test setup

- Repo HEAD: `27c710b` on branch `main`
- Python: `python3.11` (Homebrew)
- CLI: `aiswmm` v0.6.1
- Planner: `openai`
- Model: `gpt-4o-mini` (the value in `~/.aiswmm/config.toml` was
  `gpt-5.5`, which is not a public OpenAI model; overridden on
  the command line)
- Max steps: 12
- `AISWMM_OPENAI_MOCK_RESPONSE`: explicitly unset before the run
- `OPENAI_API_KEY`: loaded from the user's `~/.aiswmm/env` for the
  subprocess only; never logged

## Prompt (English, non-sensitive)

> Run the SWMM input file at `examples/tecnopolo/tecnopolo_r1_199401.inp`
> and then produce an audit report including the peak flow. Use the
> `run_swmm_inp` tool first, then `audit_run`.

## Result

**8 of 9 planned tool calls succeeded.** SWMM executed end-to-end
through the agent. The 9th step (audit via MCP) failed due to a
runtime-config drift unrelated to the agent layer itself; the
audit chain was completed afterwards via the deterministic
`aiswmm audit` CLI subcommand.

### Tool trace summary

| # | Tool | Outcome |
| --- | --- | --- |
| 1 | `list_skills` | ok — 14 skills available |
| 2 | `read_skill` (swmm-end-to-end) | ok |
| 3 | `read_skill` (swmm-runner) | ok |
| 4 | `read_skill` (swmm-experiment-audit) | ok |
| 5 | `list_mcp_servers` | ok — 8 configured |
| 6 | `list_mcp_tools` (swmm-runner) | ok — 4 tools mapped |
| 7 | `select_workflow_mode` | ok — `mode=prepared_inp_cli`, `missing=0` |
| 8 | `run_swmm_inp` | ok — swmm-runner.swmm_run completed |
| 9 | `audit_run` | **failed** — see "Known issue" below |

The planner produced a real OpenAI response id (redacted as
`resp_***` in this document) rather than the `mock-response-final`
sentinel that CI test sessions emit. The planner also followed a
sensible plan: read the relevant skill contracts before choosing a
workflow mode, then verified missing inputs before executing.

### SWMM execution result

Captured by `swmm5 v5.2.4` via the swmm-runner MCP server:

| Metric | Value | Source section |
| --- | --- | --- |
| Peak flow at `OUT_0` | **0.061 CMS** at day 10, 03:15 | Node Inflow Summary / Maximum Total Inflow |
| Total inflow volume at `OUT_0` | 0.489 × 10⁶ litres | Node Inflow Summary |
| Runoff continuity error | -0.130 % | Runoff Quantity Continuity |
| Flow routing continuity error | -0.004 % | Flow Routing Continuity |
| Flow routing method | DYNWAVE | OPTIONS |
| Return code | 0 | swmm-runner manifest |

The peak-flow value matches the prepared-input benchmark recorded in
`runs/end-to-end/tecnopolo-199401-prepared/09_audit/experiment_note.md`
(same INP, same forcing, same outlet) — independent confirmation that
the agent-driven path reproduces the established benchmark.

### Audit artefacts (produced post-hoc via `aiswmm audit` CLI)

Generated into `runs/agent/agent-1778792226/09_audit/`:

| Artefact | Role |
| --- | --- |
| `experiment_note.md` | Human-readable audit note with YAML frontmatter |
| `experiment_provenance.json` | Machine-readable provenance record |
| `comparison.json` | Comparison artefact (no baseline this run) |
| `model_diagnostics.json` | Deterministic SWMM diagnostics |

Indexed artefacts in the note include `top_manifest`, `runner_rpt`,
`runner_out`, `runner_stdout`, `runner_stderr`, `model_diagnostics`,
each with SHA256 hashes recorded.

QA gates passed: `runner_outputs_exist`.

> Note: the `peak_metric_present` gate did not run for this audit
> because the agent did not invoke the `07_qa/` peak-extraction stage.
> The peak value (0.061 CMS) is verifiable directly from the SWMM
> report (`model.rpt`, Node Inflow Summary section). A follow-up
> change to wire the QA stage into the agent-driven path would
> close this gap.

## Known issue surfaced by this test

The agent's `audit_run` tool call failed with:

> `MCP transport failed: unknown MCP server: swmm-experiment-audit`

Diagnosed root cause: `~/.aiswmm/mcp.json` registers 8 MCP servers,
all rooted at a stale sibling checkout that pre-dates three newer
servers added in the current repo:

- `swmm-experiment-audit`
- `swmm-modeling-memory`
- `swmm-uncertainty`

The current repo's `mcp/` directory contains all 11 servers; the
runtime config only references the older 8. Because `audit_run`
is routed through the missing `swmm-experiment-audit` server, the
agent's audit step always fails on this configuration.

Tracked as a separate GitHub issue. Suggested fix: re-run
`aiswmm setup` from the current checkout to refresh `mcp.json`,
or hand-edit the three missing entries.

## Implications

1. **Agent layer is functional.** Earlier audit reports that
   described the agent as "weak" or "mocked" were based on CI
   smoke artefacts and missed the single-shot agent code path
   under `runs/agent/agent-<ts>/`. With a real OpenAI key and
   without `AISWMM_OPENAI_MOCK_RESPONSE`, the agent loop calls
   tools and executes SWMM as advertised.

2. **The README claim of "natural-language orchestration with
   deterministic SWMM execution" is now backed by on-disk
   evidence** for the first time in the public history of the
   repo.

3. **The audit chain is reachable**, just not yet end-to-end
   through the agent on this user's machine, because of the
   `mcp.json` drift. Once the runtime config is refreshed, the
   same prompt should complete all 9 steps in a single agent
   session.

4. **Evidence integrity is preserved** by the post-hoc CLI
   audit: every artefact in the index carries a SHA256 hash, the
   git HEAD at audit time is recorded (`27c710bf...`), and the
   QA gate `runner_outputs_exist` is verifiable from the
   filesystem.

## Reproducing this test

```bash
# From the repo root
set -a && . ~/.aiswmm/env && set +a
unset AISWMM_OPENAI_MOCK_RESPONSE

python3.11 -m agentic_swmm.cli agent \
  --planner openai \
  --model gpt-4o-mini \
  --max-steps 12 \
  "Run the SWMM input file at examples/tecnopolo/tecnopolo_r1_199401.inp \
   and then produce an audit report including the peak flow. \
   Use the run_swmm_inp tool first, then audit_run."

# After the agent run, produce the audit deterministically
python3.11 -m agentic_swmm.cli audit \
  --run-dir runs/agent/agent-<session-id> \
  --workflow-mode prepared_inp_cli \
  --case-name "agent-nl-swmm-demo" \
  --objective "Verify natural-language driven SWMM execution + audit chain"
```

API cost for the demonstrated run (gpt-4o-mini, ~9 tool calls,
prompt + intermediate context): under USD 0.05.

## Follow-ups

- Refresh `~/.aiswmm/mcp.json` so the three newer MCP servers
  (`swmm-experiment-audit`, `swmm-modeling-memory`,
  `swmm-uncertainty`) become routable from the agent.
- Default `[openai].model` in the example `config.toml` should be
  changed to a real public OpenAI model name.
- Audit hook recorded zero entries in the `Workflow Trace` table
  (`Commands recorded: 0`), consistent with the same pattern seen
  in the prepared-input benchmark (`Commands recorded: 4` but all
  stage rows are `None`). This is an unrelated audit-hook gap, not
  an agent-layer issue.
