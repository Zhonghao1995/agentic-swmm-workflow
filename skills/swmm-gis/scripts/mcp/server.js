#!/usr/bin/env node
/** MCP server for swmm-gis skill.
 * Tools:
 * - gis_find_pour_point
 * - gis_preprocess_subcatchments
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
const pourPointPy = path.resolve(__dirname, "../find_pour_point.py");
const preprocessPy = path.resolve(__dirname, "../preprocess_subcatchments.py");

function runPy(scriptPath, args) {
  return new Promise((resolve, reject) => {
    const p = spawn("python3", [scriptPath, ...args], { stdio: ["ignore", "pipe", "pipe"] });
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

const PourArgs = z.object({
  dem: z.string(),
  method: z.enum(["boundary_min_elev", "boundary_max_accum"]).default("boundary_min_elev"),
  outGeojson: z.string(),
  outPng: z.string(),
  name: z.string().default("pour_point")
});

const PreprocessArgs = z.object({
  subcatchmentsGeojson: z.string(),
  networkJson: z.string(),
  outCsv: z.string(),
  outJson: z.string(),
  idField: z.string().default("subcatchment_id"),
  outletHintField: z.string().default("outlet_hint"),
  demStatsJson: z.string().optional(),
  demStatsIdField: z.string().default("subcatchment_id"),
  defaultSlopePct: z.number().positive().optional(),
  minSlopePct: z.number().positive().optional(),
  minWidthM: z.number().positive().optional(),
  defaultCurbLengthM: z.number().min(0).optional(),
  defaultRainGage: z.string().optional(),
  maxLinkDistanceM: z.number().positive().optional(),
});

const server = new Server(
  { name: "swmm-gis-mcp", version: "0.2.0" },
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
      },
      {
        name: "gis_preprocess_subcatchments",
        description: "Convert subcatchment polygon GeoJSON + network JSON into builder-ready subcatchment CSV with deterministic width/slope/outlet linking.",
        inputSchema: {
          type: "object",
          properties: {
            subcatchmentsGeojson: { type: "string" },
            networkJson: { type: "string" },
            outCsv: { type: "string" },
            outJson: { type: "string" },
            idField: { type: "string", default: "subcatchment_id" },
            outletHintField: { type: "string", default: "outlet_hint" },
            demStatsJson: { type: "string" },
            demStatsIdField: { type: "string", default: "subcatchment_id" },
            defaultSlopePct: { type: "number", minimum: 0.000001 },
            minSlopePct: { type: "number", minimum: 0.000001 },
            minWidthM: { type: "number", minimum: 0.000001 },
            defaultCurbLengthM: { type: "number", minimum: 0 },
            defaultRainGage: { type: "string" },
            maxLinkDistanceM: { type: "number", minimum: 0.000001 }
          },
          required: ["subcatchmentsGeojson", "networkJson", "outCsv", "outJson"]
        }
      }
    ]
  };
});

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: raw } = req.params;

  if (name === "gis_find_pour_point") {
    const a = PourArgs.parse(raw ?? {});

    fs.mkdirSync(path.dirname(a.outGeojson), { recursive: true });
    fs.mkdirSync(path.dirname(a.outPng), { recursive: true });

    const stdout = await runPy(pourPointPy, [
      "--dem", a.dem,
      "--method", a.method,
      "--out-geojson", a.outGeojson,
      "--out-png", a.outPng,
      "--name", a.name
    ]);

    return { content: [{ type: "text", text: stdout }] };
  }

  if (name === "gis_preprocess_subcatchments") {
    const a = PreprocessArgs.parse(raw ?? {});

    fs.mkdirSync(path.dirname(a.outCsv), { recursive: true });
    fs.mkdirSync(path.dirname(a.outJson), { recursive: true });

    const args = [
      "--subcatchments-geojson", a.subcatchmentsGeojson,
      "--network-json", a.networkJson,
      "--out-csv", a.outCsv,
      "--out-json", a.outJson,
      "--id-field", a.idField,
      "--outlet-hint-field", a.outletHintField,
      "--dem-stats-id-field", a.demStatsIdField,
    ];
    if (a.demStatsJson !== undefined) args.push("--dem-stats-json", a.demStatsJson);
    if (a.defaultSlopePct !== undefined) args.push("--default-slope-pct", String(a.defaultSlopePct));
    if (a.minSlopePct !== undefined) args.push("--min-slope-pct", String(a.minSlopePct));
    if (a.minWidthM !== undefined) args.push("--min-width-m", String(a.minWidthM));
    if (a.defaultCurbLengthM !== undefined) args.push("--default-curb-length-m", String(a.defaultCurbLengthM));
    if (a.defaultRainGage !== undefined) args.push("--default-rain-gage", a.defaultRainGage);
    if (a.maxLinkDistanceM !== undefined) args.push("--max-link-distance-m", String(a.maxLinkDistanceM));

    const stdout = await runPy(preprocessPy, args);
    return { content: [{ type: "text", text: stdout }] };
  }

  throw new Error(`Unknown tool: ${name}`);
});

const transport = new StdioServerTransport();
await server.connect(transport);
