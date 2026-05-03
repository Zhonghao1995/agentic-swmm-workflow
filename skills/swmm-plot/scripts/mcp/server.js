#!/usr/bin/env node
/** MCP server for swmm-plot skill.
 * Tool: plot_rain_runoff_si
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
const plotPy = path.resolve(__dirname, "../plot_rain_runoff_si.py");

function runPy(args) {
  return new Promise((resolve, reject) => {
    const p = spawn("python3", [plotPy, ...args], { stdio: ["ignore", "pipe", "pipe"] });
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

const Args = z.object({
  inp: z.string(),
  out: z.string(),
  outPng: z.string(),
  rainTs: z.string().default("TS_RAIN"),
  rainKind: z.enum(["intensity_mm_per_hr", "depth_mm_per_dt", "cumulative_depth_mm"]).default("depth_mm_per_dt"),
  dtMin: z.number().default(5),
  node: z.string().default("O1"),
  nodeAttr: z.string().default("Total_inflow"),
  dpi: z.number().default(300),
  focusDay: z.string().optional(),
  windowStart: z.string().optional(),
  windowEnd: z.string().optional(),
  padHours: z.number().default(2)
});

const server = new Server(
  { name: "swmm-plot-mcp", version: "0.1.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: "plot_rain_runoff_si",
        description: "Publication-style rainfall (inverted) vs outfall hydrograph plot (SI, mm/5min default, Arial 12, inward ticks, no title).",
        inputSchema: {
          type: "object",
          properties: {
            inp: { type: "string" },
            out: { type: "string" },
            outPng: { type: "string" },
            rainTs: { type: "string", default: "TS_RAIN" },
            rainKind: { type: "string", enum: ["intensity_mm_per_hr", "depth_mm_per_dt", "cumulative_depth_mm"], default: "depth_mm_per_dt" },
            dtMin: { type: "number", default: 5 },
            node: { type: "string", default: "O1" },
            nodeAttr: { type: "string", default: "Total_inflow" },
            dpi: { type: "number", default: 300 },
            focusDay: { type: "string" },
            windowStart: { type: "string" },
            windowEnd: { type: "string" },
            padHours: { type: "number", default: 2 }
          },
          required: ["inp", "out", "outPng"]
        }
      }
    ]
  };
});

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: raw } = req.params;
  if (name !== "plot_rain_runoff_si") throw new Error(`Unknown tool: ${name}`);
  const a = Args.parse(raw ?? {});

  fs.mkdirSync(path.dirname(a.outPng), { recursive: true });

  const pyArgs = [
    "--inp", a.inp,
    "--out", a.out,
    "--out-png", a.outPng,
    "--rain-ts", a.rainTs,
    "--rain-kind", a.rainKind,
    "--dt-min", String(a.dtMin),
    "--node", a.node,
    "--node-attr", a.nodeAttr,
    "--dpi", String(a.dpi)
  ];

  if (a.focusDay) {
    pyArgs.push("--focus-day", a.focusDay);
    if (a.windowStart && a.windowEnd) {
      pyArgs.push("--window-start", a.windowStart);
      pyArgs.push("--window-end", a.windowEnd);
    }
  } else {
    pyArgs.push("--pad-hours", String(a.padHours));
  }

  await runPy(pyArgs);

  return { content: [{ type: "text", text: JSON.stringify({ ok: true, outPng: a.outPng }, null, 2) }] };
});

const transport = new StdioServerTransport();
await server.connect(transport);
