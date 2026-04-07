#!/usr/bin/env node
/** MCP server for swmm-calibration skill.
 * Tools:
 * - swmm_sensitivity_scan
 * - swmm_calibrate
 * - swmm_validate
 * - swmm_parameter_scout
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
const calibratePy = path.resolve(__dirname, "../swmm_calibrate.py");
const scoutPy = path.resolve(__dirname, "../parameter_scout.py");

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

const Common = z.object({
  baseInp: z.string(),
  patchMap: z.string(),
  observed: z.string(),
  runRoot: z.string(),
  swmmNode: z.string().default("O1"),
  swmmAttr: z.string().default("Total_inflow"),
  objective: z.enum(["nse", "rmse", "bias", "peak_flow_error", "peak_timing_error"]).default("nse"),
  timestampCol: z.string().optional(),
  flowCol: z.string().optional(),
  timeFormat: z.string().optional(),
  summaryJson: z.string(),
  dryRun: z.boolean().default(false),
});

const SensitivityArgs = Common.extend({ parameterSets: z.string() });
const CalibrateArgs = Common.extend({ parameterSets: z.string(), bestParamsOut: z.string().optional() });
const ValidateArgs = Common.extend({ bestParams: z.string(), trialName: z.string().default("validation") });
const ScoutArgs = z.object({
  baseInp: z.string(),
  patchMap: z.string(),
  baseParams: z.string(),
  scanSpec: z.string(),
  observed: z.string(),
  runRoot: z.string(),
  summaryJson: z.string(),
  swmmNode: z.string().default("O1"),
  swmmAttr: z.string().default("Total_inflow"),
  aggregate: z.enum(["none", "daily_mean"]).default("none"),
  timestampCol: z.string().optional(),
  flowCol: z.string().optional(),
  timeFormat: z.string().optional(),
});

function commonArgs(a) {
  const out = [
    "--base-inp", a.baseInp,
    "--patch-map", a.patchMap,
    "--observed", a.observed,
    "--run-root", a.runRoot,
    "--swmm-node", a.swmmNode,
    "--swmm-attr", a.swmmAttr,
    "--objective", a.objective,
    "--summary-json", a.summaryJson,
  ];
  if (a.timestampCol) out.push("--timestamp-col", a.timestampCol);
  if (a.flowCol) out.push("--flow-col", a.flowCol);
  if (a.timeFormat) out.push("--time-format", a.timeFormat);
  if (a.dryRun) out.push("--dry-run");
  return out;
}

const server = new Server({ name: "swmm-calibration-mcp", version: "0.2.0" }, { capabilities: { tools: {} } });

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "swmm_sensitivity_scan",
      description: "Evaluate many explicit parameter sets against observed flow and rank them.",
      inputSchema: {
        type: "object",
        properties: {
          baseInp: { type: "string" }, patchMap: { type: "string" }, parameterSets: { type: "string" },
          observed: { type: "string" }, runRoot: { type: "string" }, swmmNode: { type: "string", default: "O1" },
          swmmAttr: { type: "string", default: "Total_inflow" }, objective: { type: "string", default: "nse" },
          timestampCol: { type: "string" }, flowCol: { type: "string" }, timeFormat: { type: "string" },
          summaryJson: { type: "string" }, dryRun: { type: "boolean", default: false }
        },
        required: ["baseInp", "patchMap", "parameterSets", "observed", "runRoot", "summaryJson"]
      }
    },
    {
      name: "swmm_calibrate",
      description: "Evaluate explicit candidate parameter sets and report the best one for the chosen objective.",
      inputSchema: {
        type: "object",
        properties: {
          baseInp: { type: "string" }, patchMap: { type: "string" }, parameterSets: { type: "string" },
          observed: { type: "string" }, runRoot: { type: "string" }, swmmNode: { type: "string", default: "O1" },
          swmmAttr: { type: "string", default: "Total_inflow" }, objective: { type: "string", default: "nse" },
          timestampCol: { type: "string" }, flowCol: { type: "string" }, timeFormat: { type: "string" },
          summaryJson: { type: "string" }, dryRun: { type: "boolean", default: false }, bestParamsOut: { type: "string" }
        },
        required: ["baseInp", "patchMap", "parameterSets", "observed", "runRoot", "summaryJson"]
      }
    },
    {
      name: "swmm_validate",
      description: "Apply one chosen parameter set to a second event and score the validation run.",
      inputSchema: {
        type: "object",
        properties: {
          baseInp: { type: "string" }, patchMap: { type: "string" }, bestParams: { type: "string" },
          observed: { type: "string" }, runRoot: { type: "string" }, swmmNode: { type: "string", default: "O1" },
          swmmAttr: { type: "string", default: "Total_inflow" }, objective: { type: "string", default: "nse" },
          timestampCol: { type: "string" }, flowCol: { type: "string" }, timeFormat: { type: "string" },
          summaryJson: { type: "string" }, dryRun: { type: "boolean", default: false }, trialName: { type: "string", default: "validation" }
        },
        required: ["baseInp", "patchMap", "bestParams", "observed", "runRoot", "summaryJson"]
      }
    },
    {
      name: "swmm_parameter_scout",
      description: "Rank one-parameter-at-a-time influence around a baseline and suggest direction plus a narrowed next range.",
      inputSchema: {
        type: "object",
        properties: {
          baseInp: { type: "string" }, patchMap: { type: "string" }, baseParams: { type: "string" }, scanSpec: { type: "string" },
          observed: { type: "string" }, runRoot: { type: "string" }, summaryJson: { type: "string" },
          swmmNode: { type: "string", default: "O1" }, swmmAttr: { type: "string", default: "Total_inflow" },
          aggregate: { type: "string", enum: ["none", "daily_mean"], default: "none" },
          timestampCol: { type: "string" }, flowCol: { type: "string" }, timeFormat: { type: "string" }
        },
        required: ["baseInp", "patchMap", "baseParams", "scanSpec", "observed", "runRoot", "summaryJson"]
      }
    }
  ]
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const name = req.params.name;
  const args = req.params.arguments || {};

  if (name === "swmm_sensitivity_scan") {
    const a = SensitivityArgs.parse(args);
    fs.mkdirSync(path.dirname(a.summaryJson), { recursive: true });
    const stdout = await runPy(calibratePy, ["sensitivity", ...commonArgs(a), "--parameter-sets", a.parameterSets]);
    return { content: [{ type: "text", text: stdout }] };
  }
  if (name === "swmm_calibrate") {
    const a = CalibrateArgs.parse(args);
    fs.mkdirSync(path.dirname(a.summaryJson), { recursive: true });
    const pyArgs = ["calibrate", ...commonArgs(a), "--parameter-sets", a.parameterSets];
    if (a.bestParamsOut) pyArgs.push("--best-params-out", a.bestParamsOut);
    const stdout = await runPy(calibratePy, pyArgs);
    return { content: [{ type: "text", text: stdout }] };
  }
  if (name === "swmm_validate") {
    const a = ValidateArgs.parse(args);
    fs.mkdirSync(path.dirname(a.summaryJson), { recursive: true });
    const pyArgs = ["validate", ...commonArgs(a), "--best-params", a.bestParams, "--trial-name", a.trialName];
    const stdout = await runPy(calibratePy, pyArgs);
    return { content: [{ type: "text", text: stdout }] };
  }
  if (name === "swmm_parameter_scout") {
    const a = ScoutArgs.parse(args);
    fs.mkdirSync(path.dirname(a.summaryJson), { recursive: true });
    const pyArgs = [
      "--base-inp", a.baseInp,
      "--patch-map", a.patchMap,
      "--base-params", a.baseParams,
      "--scan-spec", a.scanSpec,
      "--observed", a.observed,
      "--run-root", a.runRoot,
      "--summary-json", a.summaryJson,
      "--swmm-node", a.swmmNode,
      "--swmm-attr", a.swmmAttr,
      "--aggregate", a.aggregate,
    ];
    if (a.timestampCol) pyArgs.push("--timestamp-col", a.timestampCol);
    if (a.flowCol) pyArgs.push("--flow-col", a.flowCol);
    if (a.timeFormat) pyArgs.push("--time-format", a.timeFormat);
    const stdout = await runPy(scoutPy, pyArgs);
    return { content: [{ type: "text", text: stdout }] };
  }
  throw new Error(`Unknown tool: ${name}`);
});

const transport = new StdioServerTransport();
await server.connect(transport);
