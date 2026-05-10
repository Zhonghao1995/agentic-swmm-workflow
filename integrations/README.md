# Agentic SWMM Integrations

This directory contains runtime integration guidance for agent systems that use Agentic SWMM outside the direct CLI path.

The main software entrypoint is the `agentic-swmm` CLI in `agentic_swmm/`. Integrations remain separate because each agent runtime stores tools, skills, and MCP server definitions differently.

## Contents

- `mcp/`: Model Context Protocol setup for Codex, Hermes, OpenClaw, and other stdio MCP clients.
- `skills/`: Skill installation guidance for runtimes that support local skill registries.

## Recommended Setup Order

1. Install the project dependencies.
2. Install the Python package in editable mode:

   ```bash
   python -m pip install -e .
   ```

3. Verify the CLI:

   ```bash
   agentic-swmm doctor
   ```

4. Generate MCP runtime configs:

   ```bash
   node scripts/generate_mcp_configs.mjs
   ```

5. Smoke-test MCP discovery:

   ```bash
   node scripts/smoke_mcp_servers.mjs
   ```

6. Install or preload skills according to the target runtime.

## Boundary

The CLI is the stable public execution interface. Skills define agent behavior and evidence boundaries. MCP servers expose lower-level module tools for agent runtimes. These layers are complementary and should not be collapsed into a single interface.
