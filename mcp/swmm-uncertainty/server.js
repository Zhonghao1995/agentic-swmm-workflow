#!/usr/bin/env node
/** MCP server for swmm-uncertainty skill.
 *
 * Tools (slice 4, issue #49):
 * - swmm_sensitivity_oat     One-at-a-time sensitivity (legacy parameter_scout).
 * - swmm_sensitivity_morris  Morris elementary-effects (SALib).
 * - swmm_sensitivity_sobol   Sobol' first-order + total-effect indices (SALib).
 *
 * All three are thin wrappers around
 * `skills/swmm-uncertainty/scripts/sensitivity.py --method {oat,morris,sobol}`.
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
const sensitivityPy = path.resolve(
  __dirname,
  "../../skills/swmm-uncertainty/scripts/sensitivity.py",
);

function runPy(scriptPath, args) {
  return new Promise((resolve, reject) => {
    const p = spawn("python3", [scriptPath, ...args], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    p.stdout.on("data", (d) => (stdout += d.toString()));
    p.stderr.on("data", (d) => (stderr += d.toString()));
    p.on("close", (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`python exited ${code}\n${stderr}`));
    });
  });
}

// ----- argument schemas -----------------------------------------------------

const CommonArgs = z.object({
  baseInp: z.string(),
  patchMap: z.string(),
  observed: z.string(),
  runRoot: z.string(),
  summaryJson: z.string(),
  swmmNode: z.string().default("O1"),
  swmmAttr: z.string().default("Total_inflow"),
  aggregate: z.enum(["none", "daily_mean"]).default("none"),
  timestampCol: z.string().optional(),
  flowCol: z.string().optional(),
  timeFormat: z.string().optional(),
  obsStart: z.string().optional(),
  obsEnd: z.string().optional(),
  seed: z.number().int().default(42),
});

const OatArgs = CommonArgs.extend({
  baseParams: z.string(),
  scanSpec: z.string(),
});
const MorrisArgs = CommonArgs.extend({
  parameterSpace: z.string(),
  morrisR: z.number().int().positive().default(10),
  morrisLevels: z.number().int().positive().default(4),
});
const SobolArgs = CommonArgs.extend({
  parameterSpace: z.string(),
  sobolN: z.number().int().positive().default(256),
});

function commonArgs(a) {
  const out = [
    "--base-inp", a.baseInp,
    "--patch-map", a.patchMap,
    "--observed", a.observed,
    "--run-root", a.runRoot,
    "--summary-json", a.summaryJson,
    "--swmm-node", a.swmmNode,
    "--swmm-attr", a.swmmAttr,
    "--aggregate", a.aggregate,
    "--seed", String(a.seed),
  ];
  if (a.timestampCol) out.push("--timestamp-col", a.timestampCol);
  if (a.flowCol) out.push("--flow-col", a.flowCol);
  if (a.timeFormat) out.push("--time-format", a.timeFormat);
  if (a.obsStart) out.push("--obs-start", a.obsStart);
  if (a.obsEnd) out.push("--obs-end", a.obsEnd);
  return out;
}

// ----- server ----------------------------------------------------------------

const server = new Server(
  { name: "swmm-uncertainty-mcp", version: "0.1.0" },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "swmm_sensitivity_oat",
      description:
        "One-at-a-time (OAT) sensitivity: perturb each parameter around a baseline and rank by RMSE+peak-error spread. Backs the legacy parameter_scout workflow at the new home (swmm-uncertainty).",
      inputSchema: {
        type: "object",
        properties: {
          baseInp: { type: "string" },
          patchMap: { type: "string" },
          baseParams: { type: "string", description: "JSON object of baseline parameter values." },
          scanSpec: { type: "string", description: "JSON object: parameter -> list of trial values." },
          observed: { type: "string" },
          runRoot: { type: "string" },
          summaryJson: { type: "string", description: "Where to write sensitivity_indices.json." },
          swmmNode: { type: "string", default: "O1" },
          swmmAttr: { type: "string", default: "Total_inflow" },
          aggregate: { type: "string", enum: ["none", "daily_mean"], default: "none" },
          timestampCol: { type: "string" },
          flowCol: { type: "string" },
          timeFormat: { type: "string" },
          obsStart: { type: "string" },
          obsEnd: { type: "string" },
          seed: { type: "integer", default: 42 },
        },
        required: [
          "baseInp",
          "patchMap",
          "baseParams",
          "scanSpec",
          "observed",
          "runRoot",
          "summaryJson",
        ],
      },
    },
    {
      name: "swmm_sensitivity_morris",
      description:
        "Morris elementary-effects sensitivity analysis via SALib. Produces per-parameter mu_star + sigma at sample budget r*(k+1).",
      inputSchema: {
        type: "object",
        properties: {
          baseInp: { type: "string" },
          patchMap: { type: "string" },
          parameterSpace: {
            type: "string",
            description: "JSON object: parameter -> {min, max} bounds.",
          },
          observed: { type: "string" },
          runRoot: { type: "string" },
          summaryJson: { type: "string" },
          swmmNode: { type: "string", default: "O1" },
          swmmAttr: { type: "string", default: "Total_inflow" },
          aggregate: { type: "string", enum: ["none", "daily_mean"], default: "none" },
          timestampCol: { type: "string" },
          flowCol: { type: "string" },
          timeFormat: { type: "string" },
          obsStart: { type: "string" },
          obsEnd: { type: "string" },
          seed: { type: "integer", default: 42 },
          morrisR: {
            type: "integer",
            default: 10,
            description: "Number of trajectories; budget = r*(k+1).",
          },
          morrisLevels: { type: "integer", default: 4 },
        },
        required: [
          "baseInp",
          "patchMap",
          "parameterSpace",
          "observed",
          "runRoot",
          "summaryJson",
        ],
      },
    },
    {
      name: "swmm_sensitivity_sobol",
      description:
        "Sobol' variance-based sensitivity analysis via SALib (Saltelli sampling). Produces per-parameter first-order S_i + total-effect S_T_i at sample budget N*(2k+2).",
      inputSchema: {
        type: "object",
        properties: {
          baseInp: { type: "string" },
          patchMap: { type: "string" },
          parameterSpace: {
            type: "string",
            description: "JSON object: parameter -> {min, max} bounds.",
          },
          observed: { type: "string" },
          runRoot: { type: "string" },
          summaryJson: { type: "string" },
          swmmNode: { type: "string", default: "O1" },
          swmmAttr: { type: "string", default: "Total_inflow" },
          aggregate: { type: "string", enum: ["none", "daily_mean"], default: "none" },
          timestampCol: { type: "string" },
          flowCol: { type: "string" },
          timeFormat: { type: "string" },
          obsStart: { type: "string" },
          obsEnd: { type: "string" },
          seed: { type: "integer", default: 42 },
          sobolN: {
            type: "integer",
            default: 256,
            description: "Saltelli base sample size; budget = N*(2k+2).",
          },
        },
        required: [
          "baseInp",
          "patchMap",
          "parameterSpace",
          "observed",
          "runRoot",
          "summaryJson",
        ],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const name = req.params.name;
  const args = req.params.arguments || {};

  if (name === "swmm_sensitivity_oat") {
    const a = OatArgs.parse(args);
    fs.mkdirSync(path.dirname(a.summaryJson), { recursive: true });
    const pyArgs = [
      "--method", "oat",
      ...commonArgs(a),
      "--base-params", a.baseParams,
      "--scan-spec", a.scanSpec,
    ];
    const stdout = await runPy(sensitivityPy, pyArgs);
    return { content: [{ type: "text", text: stdout }] };
  }
  if (name === "swmm_sensitivity_morris") {
    const a = MorrisArgs.parse(args);
    fs.mkdirSync(path.dirname(a.summaryJson), { recursive: true });
    const pyArgs = [
      "--method", "morris",
      ...commonArgs(a),
      "--parameter-space", a.parameterSpace,
      "--morris-r", String(a.morrisR),
      "--morris-levels", String(a.morrisLevels),
    ];
    const stdout = await runPy(sensitivityPy, pyArgs);
    return { content: [{ type: "text", text: stdout }] };
  }
  if (name === "swmm_sensitivity_sobol") {
    const a = SobolArgs.parse(args);
    fs.mkdirSync(path.dirname(a.summaryJson), { recursive: true });
    const pyArgs = [
      "--method", "sobol",
      ...commonArgs(a),
      "--parameter-space", a.parameterSpace,
      "--sobol-n", String(a.sobolN),
    ];
    const stdout = await runPy(sensitivityPy, pyArgs);
    return { content: [{ type: "text", text: stdout }] };
  }
  throw new Error(`Unknown tool: ${name}`);
});

const transport = new StdioServerTransport();
await server.connect(transport);
