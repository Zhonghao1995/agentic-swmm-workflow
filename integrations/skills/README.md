# Skill Installation

Agentic SWMM ships its orchestration knowledge as repository skills under `skills/`.

For Codex, install them into the user's Codex skill registry:

```bash
node scripts/install_codex_skills.mjs
```

Then restart Codex so it discovers the new skills.

To preview what will be installed:

```bash
node scripts/install_codex_skills.mjs --list
```

To install into a test or custom directory:

```bash
node scripts/install_codex_skills.mjs --dest /path/to/skills
```

To replace existing Agentic SWMM skill copies:

```bash
node scripts/install_codex_skills.mjs --force
```

## What Gets Installed

The installer copies every `skills/*` directory that contains `SKILL.md`, including:

- `swmm-end-to-end`
- `swmm-builder`
- `swmm-runner`
- `swmm-gis`
- `swmm-network`
- `swmm-climate`
- `swmm-params`
- `swmm-plot`
- `swmm-calibration`
- `swmm-experiment-audit`
- `swmm-modeling-memory`
- `swmm-uncertainty`

The copied skill directories include their local scripts and any nested `scripts/mcp/` code.

## Skill And MCP Boundary

Skills and MCP servers are related but not the same runtime object.

- A skill tells the agent what the workflow is, when to call tools, what evidence boundaries matter, and when to stop.
- An MCP server exposes executable tools over the Model Context Protocol.

Agentic SWMM skills contain or reference MCP server code, but most agent runtimes do not automatically register MCP servers just because a skill was installed.

The recommended setup is:

1. Install the project.
2. Install skills with `node scripts/install_codex_skills.mjs`.
3. Register MCP servers with `node scripts/generate_mcp_configs.mjs`.
4. Verify MCP discovery with `node scripts/smoke_mcp_servers.mjs`.

For Hermes and OpenClaw, keep the skill and memory files as explicit preload/context inputs, then register the MCP servers using the generated runtime config.

## Memory Preload

Before using `swmm-end-to-end`, load the public memory files:

1. `agentic-ai/memory/identification_memory.md`
2. `agentic-ai/memory/soul.md`
3. `agentic-ai/memory/operational_memory.md`
4. `agentic-ai/memory/modeling_workflow_memory.md`
5. `agentic-ai/memory/evidence_memory.md`
6. `agentic-ai/memory/user_bridge_memory.md`

Those files are startup context, not executable tools.

