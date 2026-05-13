# Agentic SWMM Public Agent Memory Layer

This folder contains lightweight Markdown memory files for OpenClaw, Hermes, or another compatible agent runtime that needs to use Agentic SWMM from the public GitHub repository with minimal setup.

These files are not executable code and are not a replacement for `skills/swmm-end-to-end/SKILL.md`. They are a compact public project context pack that should be loaded before the top-level SWMM orchestration skill so the agent starts with the right identity, operating rules, and evidence boundaries.

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
10. `memory/modeling-memory/`

## Intended interface position

OpenClaw, Hermes, or another compatible runtime should place this memory layer between the general agent runtime and the Agentic SWMM skill layer:

```text
public agent runtime
  -> compact startup memory
  -> skills/swmm-end-to-end/SKILL.md
  -> module skills and MCP tools
  -> deterministic Python/SWMM execution
  -> audit artifacts
  -> optional modeling-memory summaries
```

The memory layer should shape decisions and communication for repository users. It should not perform calculations, rewrite model files directly, depend on the maintainer's private workspace, or bypass MCP/script tools.

`memory/modeling-memory/` is generated project memory, not startup instruction memory. Load or inspect it when the user asks for lessons learned, repeated failure patterns, missing evidence, QA issues, or controlled skill-refinement proposals.

## Minimum memory contract

An agent that loads these files should:

- know that Agentic SWMM is a reproducible stormwater modelling workflow, not a chat-to-INP toy,
- choose the top-level `swmm-end-to-end` skill for full workflow orchestration,
- infer the workflow mode from `goal -> available inputs -> missing evidence` instead of forcing the user to choose an internal mode first,
- stop on missing critical inputs rather than fabricate hydrologic or network data,
- preserve run artifacts under explicit run directories,
- run the audit layer after success, failure, or early stop,
- communicate evidence boundaries clearly to the user.
