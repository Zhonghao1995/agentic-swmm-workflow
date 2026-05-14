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
const runnerPy = path.resolve(__dirname, "../../skills/swmm-runner/scripts/swmm_runner.py");

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
  node: z.string().optional(),
  rptName: z.string().optional(),
  outName: z.string().optional(),
});
const RptNodeArgs = z.object({ rpt: z.string(), node: z.string() });

function detectFirstOutfall(inpPath) {
  const text = fs.readFileSync(inpPath, "utf8");
  const lines = text.split(/\r?\n/);
  let inSection = false;
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("[")) {
      inSection = trimmed.toUpperCase() === "[OUTFALLS]";
      continue;
    }
    if (!inSection) continue;
    if (!trimmed || trimmed.startsWith(";")) continue;
    const token = trimmed.split(/\s+/)[0];
    if (token) return token;
  }
  return null;
}
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
        description: "Run swmm5 on an INP and write rpt/out + manifest.json into runDir. When 'node' is omitted, auto-detect the first entry of the .inp [OUTFALLS] section so the manifest's peak metric targets the real outfall.",
        inputSchema: {
          type: "object",
          properties: {
            inp: { type: "string" },
            runDir: { type: "string" },
            node: { type: "string", description: "Optional. If omitted, the first [OUTFALLS] entry of the .inp is used." },
            rptName: { type: "string" },
            outName: { type: "string" }
          },
          required: ["inp", "runDir"]
        }
      },
      {
        name: "swmm_peak",
        description: "Parse peak flow and time-of-peak for a specific node/outfall from a SWMM .rpt. The node name must be supplied (no default).",
        inputSchema: {
          type: "object",
          properties: { rpt: { type: "string" }, node: { type: "string" } },
          required: ["rpt", "node"]
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
    let node = a.node;
    if (!node) {
      node = detectFirstOutfall(a.inp);
      if (!node) {
        throw new Error(
          "swmm_run: 'node' not supplied and could not auto-detect from .inp [OUTFALLS]. " +
          "Either pass node explicitly or ensure the .inp has a non-empty [OUTFALLS] section."
        );
      }
    }
    const args = ["run", "--inp", a.inp, "--run-dir", a.runDir, "--node", node];
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
