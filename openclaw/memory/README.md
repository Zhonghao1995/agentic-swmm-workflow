# Agentic SWMM Public Agent Memory Layer

This folder contains lightweight Markdown memory files for OpenClaw, Hermes, or another compatible agent runtime that needs to use Agentic SWMM from the public GitHub repository with minimal setup.

These files are not executable code and are not a replacement for `skills/swmm-end-to-end/SKILL.md`. They are a public project context pack that should be loaded before the top-level SWMM orchestration skill so the agent starts with the right identity, mission, evidence rules, and user-facing behavior.

## Recommended load order

1. `identification_memory.md`
2. `soul.md`
3. `operational_memory.md`
4. `modeling_workflow_memory.md`
5. `evidence_memory.md`
6. `user_bridge_memory.md`
7. `skills/swmm-end-to-end/SKILL.md`
8. `docs/openclaw-execution-path.md`

## Intended interface position

OpenClaw, Hermes, or another compatible runtime should place this memory layer between the general agent runtime and the Agentic SWMM skill layer:

```text
public agent runtime
  -> openclaw/memory/*.md
  -> skills/swmm-end-to-end/SKILL.md
  -> module skills and MCP tools
  -> deterministic Python/SWMM execution
  -> audit artifacts
```

The memory layer should shape decisions and communication for repository users. It should not perform calculations, rewrite model files directly, depend on the maintainer's private workspace, or bypass MCP/script tools.

## Minimum memory contract

An agent that loads these files should:

- know that Agentic SWMM is a reproducible stormwater modelling workflow, not a chat-to-INP toy,
- choose the top-level `swmm-end-to-end` skill for full workflow orchestration,
- guide the user through modelling in the order `goal -> inputs -> mode -> build -> run -> QA -> audit -> readiness report`,
- stop on missing critical inputs rather than fabricate hydrologic or network data,
- preserve run artifacts under explicit run directories,
- run the audit layer after success, failure, or early stop,
- communicate evidence boundaries clearly to the user.
