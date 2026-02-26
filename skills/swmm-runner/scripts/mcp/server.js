#!/usr/bin/env node
/** MCP server for swmm-runner skill.
 * Tools:
 * - swmm_run: run swmm5 inp->rpt/out in a run directory, write manifest.json
 * - swmm_peak: parse peak flow/time from rpt
 * - swmm_continuity: parse continuity blocks from rpt
 * - swmm_compare: compare two rpt files (GUI vs CLI)
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { ListToolsRequestSchema, CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const runnerPy = path.resolve(__dirname, "../swmm_runner.py");

function runPy(args) {
  return new Promise((resolve, reject) => {
    const p = spawn("python3", [runnerPy, ...args], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    p.stdout.on("data", (d) => (stdout += d.toString()));
    p.stderr.on("data", (d) => (stderr += d.toString()));
    p.on("error", reject);
    p.on("close", (code) => {
      if (code !== 0) reject(new Error(stderr || `python rc=${code}`));
      else resolve(stdout);
    });
  });
}

const RunArgs = z.object({
  inp: z.string(),
  runDir: z.string(),
  node: z.string().default("O1"),
  rptName: z.string().optional(),
  outName: z.string().optional(),
});
const RptNodeArgs = z.object({ rpt: z.string(), node: z.string().default("O1") });
const RptArgs = z.object({ rpt: z.string() });
const CompareArgs = z.object({ rpt: z.string(), rpt2: z.string() });

const server = new Server(
  { name: "swmm-runner-mcp", version: "0.1.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: "swmm_run",
        description: "Run swmm5 on an INP and write rpt/out + manifest.json into runDir.",
        inputSchema: {
          type: "object",
          properties: {
            inp: { type: "string" },
            runDir: { type: "string" },
            node: { type: "string", default: "O1" },
            rptName: { type: "string" },
            outName: { type: "string" }
          },
          required: ["inp", "runDir"]
        }
      },
      {
        name: "swmm_peak",
        description: "Parse peak flow and time-of-peak for a node/outfall from a SWMM .rpt.",
        inputSchema: {
          type: "object",
          properties: { rpt: { type: "string" }, node: { type: "string", default: "O1" } },
          required: ["rpt"]
        }
      },
      {
        name: "swmm_continuity",
        description: "Parse Runoff Quantity / Flow Routing continuity tables from a SWMM .rpt.",
        inputSchema: {
          type: "object",
          properties: { rpt: { type: "string" } },
          required: ["rpt"]
        }
      },
      {
        name: "swmm_compare",
        description: "Compare continuity error (%) between two SWMM .rpt files (e.g., GUI vs CLI).",
        inputSchema: {
          type: "object",
          properties: { rpt: { type: "string" }, rpt2: { type: "string" } },
          required: ["rpt", "rpt2"]
        }
      }
    ]
  };
});

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: raw } = req.params;

  if (name === "swmm_run") {
    const a = RunArgs.parse(raw ?? {});
    fs.mkdirSync(a.runDir, { recursive: true });
    const args = ["run", "--inp", a.inp, "--run-dir", a.runDir, "--node", a.node];
    if (a.rptName) args.push("--rpt-name", a.rptName);
    if (a.outName) args.push("--out-name", a.outName);
    const stdout = await runPy(args);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "swmm_peak") {
    const a = RptNodeArgs.parse(raw ?? {});
    const stdout = await runPy(["peak", "--rpt", a.rpt, "--node", a.node]);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "swmm_continuity") {
    const a = RptArgs.parse(raw ?? {});
    const stdout = await runPy(["continuity", "--rpt", a.rpt]);
    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "swmm_compare") {
    const a = CompareArgs.parse(raw ?? {});
    const stdout = await runPy(["compare", "--rpt", a.rpt, "--rpt2", a.rpt2]);
    return { content: [{ type: "text", text: stdout }] };
  }

  throw new Error(`Unknown tool: ${name}`);
});

const transport = new StdioServerTransport();
await server.connect(transport);
