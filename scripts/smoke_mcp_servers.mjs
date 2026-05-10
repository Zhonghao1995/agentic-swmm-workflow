#!/usr/bin/env node
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..");
const runnerPackage = path.join(repoRoot, "skills", "swmm-runner", "scripts", "mcp", "package.json");
const requireFromRunner = createRequire(runnerPackage);

const { Client } = await import(pathToFileURL(requireFromRunner.resolve("@modelcontextprotocol/sdk/client/index.js")));
const { StdioClientTransport } = await import(pathToFileURL(requireFromRunner.resolve("@modelcontextprotocol/sdk/client/stdio.js")));

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

const launcher = path.join(repoRoot, "scripts", "run_mcp_server.mjs");
const results = [];

for (const server of servers) {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [launcher, server],
    env: process.env,
  });
  const client = new Client({ name: "agentic-swmm-mcp-smoke", version: "0.1.0" }, { capabilities: {} });
  try {
    await client.connect(transport);
    const tools = await client.listTools();
    results.push({
      server,
      ok: true,
      tool_count: tools.tools.length,
      tools: tools.tools.map((tool) => tool.name),
    });
  } catch (error) {
    results.push({
      server,
      ok: false,
      error: error?.message || String(error),
    });
  } finally {
    try {
      await client.close();
    } catch {
      // The server may already be closed after a failed startup.
    }
  }
}

const runnerResult = results.find((result) => result.server === "swmm-runner");
const rpt = path.join(repoRoot, "runs", "acceptance", "windows-python-executable-check", "05_runner", "acceptance.rpt");
if (runnerResult?.ok && fs.existsSync(rpt)) {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [launcher, "swmm-runner"],
    env: process.env,
  });
  const client = new Client({ name: "agentic-swmm-runner-call-smoke", version: "0.1.0" }, { capabilities: {} });
  try {
    await client.connect(transport);
    const peak = await client.callTool({
      name: "swmm_peak",
      arguments: { rpt, node: "OF1" },
    });
    runnerResult.call_smoke = { ok: true, tool: "swmm_peak", result: peak };
  } catch (error) {
    runnerResult.call_smoke = { ok: false, error: error?.message || String(error) };
  } finally {
    try {
      await client.close();
    } catch {
      // Ignore shutdown errors in smoke reporting.
    }
  }
}

console.log(JSON.stringify({ ok: results.every((result) => result.ok), results }, null, 2));
if (!results.every((result) => result.ok)) {
  process.exit(1);
}

