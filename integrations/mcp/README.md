# MCP Runtime Integration

This folder makes the Agentic SWMM module MCP servers easier to attach to Codex, Hermes, OpenClaw, or any other stdio MCP client.

The repository has eleven module MCP servers:

- `swmm-builder`
- `swmm-calibration`
- `swmm-climate`
- `swmm-experiment-audit`
- `swmm-gis`
- `swmm-modeling-memory`
- `swmm-network`
- `swmm-params`
- `swmm-plot`
- `swmm-runner`
- `swmm-uncertainty`

## Prerequisites

Run the normal project install first so Python dependencies, MCP dependencies, and the SWMM solver are available.

On Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Yes
```

On macOS/Linux:

```bash
bash scripts/install.sh
```

## Smoke Test

From the repository root:

```bash
node scripts/smoke_mcp_servers.mjs
```

The smoke test starts each MCP server with the repository launcher, lists its tools through the MCP protocol, and exits nonzero if any server cannot be discovered.

If an acceptance run exists at `runs/acceptance/windows-python-executable-check`, the smoke test also calls `swmm-runner.swmm_peak` as a real tool-call check.

## Runtime Launcher

Use this command shape for any stdio MCP client:

```bash
node /absolute/path/to/Agentic-SWMM/scripts/run_mcp_server.mjs swmm-runner
```

The launcher sets the server working directory, points Python tools at the repo-local `.venv` when present, and prepends `.local/bin` to `PATH` so the local `swmm5` solver can be found.

## Generate Local Configs

Generate copy-ready config files for the current checkout:

```bash
node scripts/generate_mcp_configs.mjs
```

Generated files are written to:

```text
integrations/mcp/generated/
```

They are intentionally ignored by git because they contain machine-specific absolute paths.

Generated outputs:

- `hermes-mcp.config.yaml`
- `openclaw-mcp.config.json`
- `codex-mcp-add.ps1`
- `codex-mcp-add.sh`

## Memory And Skill Preload

MCP exposes deterministic tools. The agent runtime still needs the project memory and orchestration contract to use those tools correctly.

For Codex skills, install the repository skills first:

```bash
node scripts/install_codex_skills.mjs
```

See [Skill installation](../skills/README.md).

Recommended preload order:

1. `agent/memory/identification_memory.md`
2. `agent/memory/soul.md`
3. `agent/memory/operational_memory.md`
4. `agent/memory/modeling_workflow_memory.md`
5. `agent/memory/evidence_memory.md`
6. `agent/memory/user_bridge_memory.md`
7. `skills/swmm-end-to-end/SKILL.md`
8. `docs/openclaw-execution-path.md`

## Codex

After generating configs, run the generated Codex registration script:

```powershell
.\integrations\mcp\generated\codex-mcp-add.ps1
```

or on macOS/Linux:

```bash
bash integrations/mcp/generated/codex-mcp-add.sh
```

Codex stores MCP server definitions in the user's Codex config. If a server name already exists, remove it first:

```bash
codex mcp remove swmm-runner
```

## Hermes

Copy the generated `mcp_servers:` block from:

```text
integrations/mcp/generated/hermes-mcp.config.yaml
```

into:

```text
~/.hermes/config.yaml
```

Restart Hermes after editing the config.

## OpenClaw

Merge the generated `mcp.servers` object from:

```text
integrations/mcp/generated/openclaw-mcp.config.json
```

into the OpenClaw config.

OpenClaw can also register a single server from the command line. Example:

```bash
openclaw mcp set swmm-runner '{"command":"node","args":["/absolute/path/to/Agentic-SWMM/scripts/run_mcp_server.mjs","swmm-runner"]}'
```

## Current Boundary

This integration package makes the module MCP servers discoverable and callable by standard stdio MCP clients. Skill installation is separate because runtimes store skills in different registries. For Codex, use `scripts/install_codex_skills.mjs`; for Hermes and OpenClaw, preload the memory files and `swmm-end-to-end` skill according to the runtime's skill/context mechanism.
