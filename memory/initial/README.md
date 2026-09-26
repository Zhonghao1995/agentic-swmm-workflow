# Agentic SWMM initial memory

This folder is the hand-written, shipped memory: the lightweight Markdown files that I (the aiswmm shell, under its context budget) or another compatible agent runtime such as OpenClaw or Hermes load on startup, plus the reference tables (`reference_benchmarks.yaml`, `storm_library.yaml`, `citations.yaml`). Nothing here is written by the program; it changes only through a pull request.

The rest of `memory/` is the workspace's own: `memory/store/` is what the program writes in normal use (ledgers, the session database, project overrides; gitignored), `memory/facts.md` holds promoted, human-approved project facts, and `memory/proposals/` holds pending proposals awaiting a human decision (gitignored).

These files are not executable code and are not a replacement for `skills/swmm-end-to-end/SKILL.md`. I treat them as a compact public project context pack — they get loaded before the top-level SWMM orchestration skill so I start each session with the right identity, operating rules, and evidence boundaries.

## Recommended load order

1. `identification_memory.md`
2. `operational_memory.md`
3. `evidence_memory.md`
4. `skills/swmm-end-to-end/SKILL.md`
5. `docs/openclaw-execution-path.md`

Optional references, loaded only when the task needs them:

6. `soul.md`
7. `modeling_workflow_memory.md`
8. `user_bridge_memory.md`
9. `skills/swmm-modeling-memory/SKILL.md`
10. `memory/store/` (this workspace's project memory)

## Intended interface position

OpenClaw, Hermes, or another compatible runtime places this memory layer between the general agent runtime and the Agentic SWMM skill layer:

```text
public agent runtime
  -> compact startup memory
  -> skills/swmm-end-to-end/SKILL.md
  -> module skills and MCP tools
  -> deterministic Python/SWMM execution
  -> audit artifacts
  -> optional modeling-memory summaries
```

I let the memory layer shape my decisions and communication for repository users. It should not perform calculations, rewrite model files directly, depend on the maintainer's private workspace, or bypass MCP/script tools.

`memory/store/` is generated project memory, not startup instruction memory. I load or inspect it only when you ask for lessons learned, repeated failure patterns, missing evidence, QA issues, or controlled skill-refinement proposals.

## Minimum memory contract

When I load these files, I should:

- know that Agentic SWMM is a reproducible stormwater modelling workflow, not a chat-to-INP toy,
- choose the top-level `swmm-end-to-end` skill for full workflow orchestration,
- infer the workflow mode from `goal -> available inputs -> missing evidence` instead of forcing you to choose an internal mode first,
- stop on missing critical inputs rather than fabricate hydrologic or network data,
- preserve run artifacts under explicit run directories,
- run the audit layer after success, failure, or early stop,
- communicate evidence boundaries clearly — I won't quietly upgrade a *runnable* result to *calibrated* or *validated*.
