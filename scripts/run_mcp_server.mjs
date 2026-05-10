#!/usr/bin/env node
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..");

const servers = {
  "swmm-builder": "skills/swmm-builder/scripts/mcp",
  "swmm-calibration": "skills/swmm-calibration/scripts/mcp",
  "swmm-climate": "skills/swmm-climate/scripts/mcp",
  "swmm-gis": "skills/swmm-gis/scripts/mcp",
  "swmm-network": "skills/swmm-network/scripts/mcp",
  "swmm-params": "skills/swmm-params/scripts/mcp",
  "swmm-plot": "skills/swmm-plot/scripts/mcp",
  "swmm-runner": "skills/swmm-runner/scripts/mcp",
};

const serverName = process.argv[2];
if (!serverName || !servers[serverName]) {
  console.error(`Usage: node scripts/run_mcp_server.mjs <${Object.keys(servers).join("|")}>`);
  process.exit(2);
}

const serverDir = path.join(repoRoot, servers[serverName]);
const serverJs = path.join(serverDir, "server.js");
if (!fs.existsSync(serverJs)) {
  console.error(`Missing MCP server entrypoint: ${serverJs}`);
  process.exit(2);
}

const pythonCandidates = process.platform === "win32"
  ? [path.join(repoRoot, ".venv", "Scripts", "python.exe")]
  : [path.join(repoRoot, ".venv", "bin", "python")];
const python = process.env.PYTHON || pythonCandidates.find((candidate) => fs.existsSync(candidate));

const env = { ...process.env };
if (python) {
  env.PYTHON = python;
}

const localBin = path.join(repoRoot, ".local", "bin");
if (fs.existsSync(localBin)) {
  env.PATH = `${localBin}${path.delimiter}${env.PATH || ""}`;
}

const child = spawn(process.execPath, [serverJs], {
  cwd: serverDir,
  env,
  stdio: "inherit",
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});

