#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..");
const argOut = process.argv.indexOf("--out-dir");
const outDir = path.resolve(repoRoot, argOut >= 0 ? process.argv[argOut + 1] : "integrations/mcp/generated");
const launcher = path.join(repoRoot, "scripts", "run_mcp_server.mjs");

const servers = [
  "swmm-builder",
  "swmm-calibration",
  "swmm-climate",
  "swmm-gis",
  "swmm-network",
  "swmm-params",
  "swmm-plot",
  "swmm-runner",
];

function jsonString(value) {
  return JSON.stringify(value);
}

function yamlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

fs.mkdirSync(outDir, { recursive: true });

const hermes = [
  "# Copy the mcp_servers block into ~/.hermes/config.yaml.",
  "# Generated for this checkout; keep paths quoted because they may contain spaces.",
  "mcp_servers:",
];
for (const server of servers) {
  hermes.push(`  ${server}:`);
  hermes.push(`    command: ${yamlString(process.execPath)}`);
  hermes.push(`    args: [${yamlString(launcher)}, ${yamlString(server)}]`);
  hermes.push("    enabled: true");
}
fs.writeFileSync(path.join(outDir, "hermes-mcp.config.yaml"), `${hermes.join("\n")}\n`);

const openclaw = {
  mcp: {
    servers: Object.fromEntries(servers.map((server) => [
      server,
      { command: process.execPath, args: [launcher, server] },
    ])),
  },
};
fs.writeFileSync(path.join(outDir, "openclaw-mcp.config.json"), `${JSON.stringify(openclaw, null, 2)}\n`);

const codexPs = [
  "# Run from PowerShell to register Agentic SWMM MCP servers with Codex.",
  "# Existing servers with the same names may need `codex mcp remove <name>` first.",
  ...servers.map((server) => `codex mcp add ${server} -- ${psQuote(process.execPath)} ${psQuote(launcher)} ${psQuote(server)}`),
  "",
];
fs.writeFileSync(path.join(outDir, "codex-mcp-add.ps1"), codexPs.join("\n"));

const codexSh = [
  "#!/usr/bin/env bash",
  "set -euo pipefail",
  "# Existing servers with the same names may need `codex mcp remove <name>` first.",
  ...servers.map((server) => `codex mcp add ${server} -- ${shellQuote(process.execPath)} ${shellQuote(launcher)} ${shellQuote(server)}`),
  "",
];
fs.writeFileSync(path.join(outDir, "codex-mcp-add.sh"), codexSh.join("\n"));

console.log(`Wrote MCP runtime configs to ${outDir}`);

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\\''")}'`;
}

function psQuote(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}
