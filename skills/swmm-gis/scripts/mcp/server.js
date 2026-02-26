#!/usr/bin/env node
/** MCP server for swmm-gis skill.
 * Tool: gis_find_pour_point
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
const py = path.resolve(__dirname, "../find_pour_point.py");

function runPy(args) {
  return new Promise((resolve, reject) => {
    const p = spawn("python3", [py, ...args], { stdio: ["ignore", "pipe", "pipe"] });
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
  dem: z.string(),
  method: z.enum(["boundary_min_elev", "boundary_max_accum"]).default("boundary_min_elev"),
  outGeojson: z.string(),
  outPng: z.string(),
  name: z.string().default("pour_point")
});

const server = new Server(
  { name: "swmm-gis-mcp", version: "0.1.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: "gis_find_pour_point",
        description: "Find DEM-based pour point (outlet) and export GeoJSON+preview PNG. Methods: boundary_min_elev or boundary_max_accum.",
        inputSchema: {
          type: "object",
          properties: {
            dem: { type: "string" },
            method: { type: "string", enum: ["boundary_min_elev", "boundary_max_accum"], default: "boundary_min_elev" },
            outGeojson: { type: "string" },
            outPng: { type: "string" },
            name: { type: "string", default: "pour_point" }
          },
          required: ["dem", "outGeojson", "outPng"]
        }
      }
    ]
  };
});

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: raw } = req.params;
  if (name !== "gis_find_pour_point") throw new Error(`Unknown tool: ${name}`);
  const a = Args.parse(raw ?? {});

  fs.mkdirSync(path.dirname(a.outGeojson), { recursive: true });
  fs.mkdirSync(path.dirname(a.outPng), { recursive: true });

  const stdout = await runPy([
    "--dem", a.dem,
    "--method", a.method,
    "--out-geojson", a.outGeojson,
    "--out-png", a.outPng,
    "--name", a.name
  ]);

  return { content: [{ type: "text", text: stdout }] };
});

const transport = new StdioServerTransport();
await server.connect(transport);
