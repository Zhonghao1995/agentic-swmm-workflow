# Agentic Runtime UX PRD

## Problem

The current `aiswmm` runtime can run prepared INP workflows, audit runs, inspect plot options, cache MCP schemas, and route common intents. Even so, the user experience still feels weak when the agent is used as an interactive modelling assistant.

The main failure is continuity. The agent often behaves like every prompt is a new task instead of continuing the current SWMM case. This causes repeated input selection, unnecessary restarts, unclear defaults, and confusing output.

The second failure is agency. In constrained mode, the agent can only use existing wrappers and allowlisted commands. That is useful for public reproducibility, but it feels too limited for trusted local development where the user expects the agent to write scripts, run commands, install dependencies, and adapt the workspace.

The third failure is observability. Users need a short answer, but they also need to know whether tools actually ran, which skill or MCP path was used, and where the artifacts are.

## Goals

- Continue from the previous modelling step without forcing the user to restate the INP, run folder, node, rainfall series, or plot variable.
- Preserve a stable working state organized around SWMM cases, not chat sessions.
- Ask concise clarification questions when required inputs are missing or risky.
- Keep default responses short and action-oriented.
- Show a short tool provenance summary by default, with full trace available on demand.
- Support configurable permission modes: constrained by default, trusted for local development.
- Keep SWMM evidence boundaries intact: runs, audits, plots, and memory outputs remain traceable artifacts.

## Non-Goals

- Do not make arbitrary shell execution the default behaviour.
- Do not replace deterministic SWMM skills and CLI wrappers with untracked planner text.
- Do not build a full UI before the runtime state and continuation model are reliable.
- Do not silently overwrite, delete, publish, or upload artifacts.

## Users and Workflows

Primary user:
- a local modeller or developer using `aiswmm` interactively to run, inspect, audit, plot, modify, and extend SWMM workflows.

Core workflow:
1. User points to an INP or example directory.
2. Agent runs the model and writes an evidence folder.
3. Agent audits the run.
4. User follows up with short commands such as "plot J2", "change to Total_inflow", "audit it", "summarize this", or "continue".
5. Runtime resolves these commands against the active case and active run.

Development workflow:
1. User enables trusted mode.
2. Agent can write helper scripts, run shell commands, inspect broader files, and call MCP tools more freely.
3. Writes, installations, and risky actions are traced and summarized.
4. Overwrite/delete/publish actions still require confirmation.

## Runtime State Model

State should be case-first and session-second.

### Global State

Store in `~/.aiswmm/state.json`.

Fields:
- `active_case_id`
- `active_run_dir`
- `recent_cases`
- `last_opened_at`
- `default_language`
- `mode`: `constrained` or `trusted`

Purpose:
- remember the most recent active case across interactive turns and process restarts
- route short follow-up prompts to the correct case
- avoid restarting input discovery when the user asks to continue

### Case State

Store in the run folder or case folder, for example:

```text
runs/<case-or-date>/<run>/aiswmm_state.json
```

Fields:
- `case_id`
- `source_inp`
- `active_run_dir`
- `last_successful_stage`
- `available_next_actions`
- `selected_node`
- `selected_rainfall`
- `selected_plot_variable`
- `pending_clarification`
- `tool_history`
- `artifact_index`

Purpose:
- preserve modelling context near evidence artifacts
- let future turns answer "what can I do next?"
- let the agent explain what happened without replaying all logs

### Session State

Session traces remain useful, but they are not the primary source of working context.

Session state should record:
- current prompt
- planner calls
- tool calls
- stdout/stderr paths
- final report

## Continuation Rules

The runtime should resolve short commands against working state before asking for new inputs.

Low-risk automatic continuation:
- "plot" uses the active run folder when one exists
- "audit it" uses the active run folder
- "summarize" uses the active run or active case memory
- "continue" uses the pending next action
- "switch to J2" reuses the active run and changes the selected node
- "use Total_inflow" reuses the active run and changes the selected plot variable

Clarify before continuing when:
- no active run exists
- multiple plausible active runs exist
- the requested action would modify an INP or source file
- the command could refer to more than one case
- required observed data, calibration objective, rainfall series, or node is missing

## Clarification Policy

Default mode should use balanced clarification:
- automatically fill low-risk context from working state
- ask before risky, destructive, expensive, or ambiguous actions

Trusted mode may be more aggressive:
- automatically fill context
- run exploratory commands
- write helper scripts
- retry failed commands with corrected arguments

Trusted mode still must not silently:
- overwrite original INP files
- delete files or run folders
- publish, push, or upload data
- use credentials
- run long or expensive jobs

Those actions require confirmation.

## Response Policy

The agent should follow the user's language. If the user writes Chinese, answer in Chinese. If the user writes English, answer in English. Paths, tool names, command names, and file names should remain literal.

Default style:
- concise
- result-first
- no internal reasoning trace
- no long step list unless the user asks

Recommended shapes:

Successful execution:

```text
Completed the Tecnopolo run and audit. Artifacts: runs/.... Tools: run_swmm_inp -> audit_run.
```

Missing input:

```text
I need a node before plotting. Options: J1, J2, OUT_0. Which one should I use?
```

Failure:

```text
Run failed: SWMM did not produce a .out file. Recommended recovery: run doctor to check swmm5.
```

Trusted mode write:

```text
I will write one new helper script and run pytest. I will not overwrite the original INP.
```

## Tool Provenance Policy

Tool provenance should be configurable.

Default:
- show one short line listing the tools used

Example:

```text
Tools: select_workflow_mode -> run_swmm_inp -> audit_run -> inspect_plot_options.
```

Quiet mode:
- hide successful tool provenance
- show provenance only for failure or fallback

Failure or fallback:
- always disclose the relevant tool path

Example:

```text
MCP swmm-runner.run failed. I used CLI wrapper run_swmm_inp instead. Reason: schema mismatch.
```

Full trace:
- available on demand through `agent_trace.jsonl`, `final_report.md`, and `tool_results/`

## Permission Modes

### Constrained Mode

Default mode.

Allowed:
- repository read/search/list
- prepared INP run and audit
- plot option inspection and plotting
- deterministic skill wrappers
- configured MCP calls
- allowlisted commands
- repository-local patching under current policy

Not allowed:
- arbitrary shell
- dependency installation
- unbounded external file writes
- silent source or INP mutation

### Trusted Mode

Explicit local-development mode.

Trusted mode can automatically:
- read and search broader workspace files
- write new helper scripts
- run arbitrary shell commands
- call configured MCP tools
- retry failed commands
- fill context from working state more aggressively

Trusted mode should prompt once before:
- installing dependencies
- modifying repository source files
- modifying INP files
- modifying config
- accessing workspace-external paths
- downloading data from the network

Trusted mode must confirm before:
- overwriting original INP files
- deleting files or run folders
- running long or large jobs
- using credentials or API keys
- pushing git, publishing, or uploading data

All trusted-mode actions must be traced.

## INP Understanding and Editing

The runtime should eventually consolidate scattered INP logic into a deeper module.

Existing shallow INP logic includes:
- prepared INP import and sidecar copy in `agentic_swmm.commands.run`
- plot option inspection and rainfall parsing
- node suggestions from INP files
- calibration patch-map based INP field edits
- builder-generated INP manifests

Future `swmm-inp` capabilities:
- inspect sections and object counts
- list subcatchments, nodes, conduits, outfalls, raingages, and timeseries
- validate references across sections
- produce safe patches by section/object/field
- diff before and after
- round-trip without unnecessary formatting churn

This is important, but it is not the MVP for runtime UX. Continuation comes first.

## Skill and MCP Invocation

The agent should prefer deterministic CLI wrappers for core audited workflows:
- run
- audit
- plot
- memory

The agent should use MCP when:
- a module-specific schema is clearer than the CLI wrapper
- the workflow stage has no CLI wrapper
- the user specifically asks for MCP

MCP discovery should use cached schemas when valid, refresh on request, and fall back to CLI wrappers when MCP calls fail.

The response should disclose fallback briefly.

## UI Direction

Do not build the UI first.

A useful UI depends on reliable runtime state:
- active case
- active run
- next actions
- pending clarification
- artifact index
- tool provenance

After the runtime state model is stable, a thin UI can expose:
- current case
- active run folder
- artifact list
- next-action buttons
- pending questions
- trace expansion

## MVP Scope

MVP is continuation runtime.

Included:
- global active state in `~/.aiswmm/state.json`
- case state in run folder
- active run pointer updates after successful run/audit/plot
- short follow-up prompt resolution
- clarification policy for missing context
- concise response policy
- default tool provenance summary
- fallback summary for MCP failures

Excluded from MVP:
- arbitrary shell
- dependency installation
- full trusted mode implementation
- generic INP patch/diff
- UI

## Acceptance Criteria

Continuation:
- After running a prepared INP, the user can say "plot J2" without restating the INP or run folder.
- After a plot option inspection asks for a variable, the user can reply only with the variable and the runtime continues.
- After `audit it`, the runtime uses the active run folder.
- If there is no active run, the runtime asks one concise question instead of defaulting to acceptance demo.

State:
- A successful run writes or updates case state.
- Global state points to the active case and active run.
- Restarting `aiswmm` can recover the last active run.

Output:
- Successful tasks respond with result, artifact path, and short tool provenance.
- Missing inputs produce one question.
- Failures include cause and one recommended recovery action.

Tooling:
- MCP schema cache remains used.
- MCP failure reports fallback when a CLI wrapper exists.
- Existing evidence boundaries remain visible in manifests and audit artifacts.

## Open Questions

- Should global state keep one active case or multiple named active cases?
- Should users be able to pin a case manually?
- What is the exact command or prompt for switching between recent cases?
- Should trusted mode be a CLI flag, config setting, or per-turn confirmation?
- Should quiet mode be configured globally or per session?
