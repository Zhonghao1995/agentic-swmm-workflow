#!/usr/bin/env node
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..");

const servers = {
  "swmm-builder": "mcp/swmm-builder",
  "swmm-calibration": "mcp/swmm-calibration",
  "swmm-climate": "mcp/swmm-climate",
  "swmm-experiment-audit": "mcp/swmm-experiment-audit",
  "swmm-gis": "mcp/swmm-gis",
  "swmm-modeling-memory": "mcp/swmm-modeling-memory",
  "swmm-network": "mcp/swmm-network",
  "swmm-params": "mcp/swmm-params",
  "swmm-plot": "mcp/swmm-plot",
  "swmm-runner": "mcp/swmm-runner",
  "swmm-uncertainty": "mcp/swmm-uncertainty",
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

function isUsableInterpreter(candidate) {
  // A candidate is usable only if it is a non-empty file the current
  // user can execute. A zero-byte ``.venv/bin/python`` stub (left behind
  // by a half-finished venv, a test-fixture leak, or a stray subagent
  // worktree) trips Node's ``spawn`` with ENOEXEC because the kernel
  // cannot recognise an empty file as an executable — see the
  // ``test_launcher_rejects_unusable_venv_python_stub`` regression.
  try {
    const st = fs.statSync(candidate);
    if (!st.isFile() || st.size === 0) return false;
    fs.accessSync(candidate, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

const pythonCandidates = process.platform === "win32"
  ? [path.join(repoRoot, ".venv", "Scripts", "python.exe")]
  : [path.join(repoRoot, ".venv", "bin", "python")];
const python = process.env.PYTHON || pythonCandidates.find(isUsableInterpreter);

const env = { ...process.env };
if (python) {
  env.PYTHON = python;
}

const localBin = path.join(repoRoot, ".local", "bin");
if (fs.existsSync(localBin)) {
  env.PATH = `${localBin}${path.delimiter}${env.PATH || ""}`;
}

if (process.env.AISWMM_LAUNCHER_PYTHON_PROBE) {
  // Test-only side channel: emit the resolved interpreter to stderr
  // and exit cleanly before spawning the MCP server. Used by
  // ``tests/test_run_mcp_server_launcher_coverage.py`` to lock in the
  // candidate-filter behaviour without standing up a full MCP transport.
  process.stderr.write(`PYTHON=${env.PYTHON || ""}\n`);
  process.exit(0);
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
