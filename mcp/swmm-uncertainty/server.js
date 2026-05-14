#!/usr/bin/env node
/** MCP server for swmm-uncertainty skill.
 *
 * Tools:
 * - swmm_sensitivity_oat     One-at-a-time sensitivity (legacy parameter_scout).
 * - swmm_sensitivity_morris  Morris elementary-effects (SALib).
 * - swmm_sensitivity_sobol   Sobol' first-order + total-effect indices (SALib).
 * - swmm_rainfall_ensemble   Rainfall ensemble — perturbation of an observed
 *                            series, or IDF-curve design storms with sampled
 *                            (a, b, c) parameters.
 * - swmm_uncertainty_source_decomposition  Integrate raw uncertainty outputs
 *                            (Sobol' / Morris / DREAM-ZS / SCE-UA / rainfall
 *                            ensemble / MC propagation) under <run_dir>/09_audit/
 *                            into uncertainty_source_summary.md +
 *                            uncertainty_source_decomposition.json (#55).
 *
 * Sensitivity tools wrap `skills/swmm-uncertainty/scripts/sensitivity.py`.
 * The rainfall ensemble wraps `skills/swmm-uncertainty/scripts/rainfall_ensemble.py`.
 * Source decomposition wraps `skills/swmm-uncertainty/scripts/source_decomposition.py`.
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
const rainfallEnsemblePy = path.resolve(
  __dirname,
  "../../skills/swmm-uncertainty/scripts/rainfall_ensemble.py",
);
const sourceDecompositionPy = path.resolve(
  __dirname,
  "../../skills/swmm-uncertainty/scripts/source_decomposition.py",
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

const RainfallEnsembleArgs = z.object({
  method: z.enum(["perturbation", "idf"]),
  config: z.string(),
  runRoot: z.string(),
  baseInp: z.string().optional(),
  seriesName: z.string().default("TS_RAIN"),
  swmmNode: z.string().default("O1"),
  seed: z.number().int().default(42),
  dryRun: z.boolean().default(false),
});

const SourceDecompositionArgs = z.object({
  runDir: z.string(),
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
    {
      name: "swmm_uncertainty_source_decomposition",
      description:
        "Integrate raw uncertainty outputs in <runDir>/09_audit/ (Sobol' / Morris / DREAM-ZS / SCE-UA / rainfall ensemble / MC propagation) into uncertainty_source_summary.md + uncertainty_source_decomposition.json (schema_version 1.0). Pure function; safe to re-invoke; emits an Evidence Boundary table so no method is silently absent.",
      inputSchema: {
        type: "object",
        properties: {
          runDir: {
            type: "string",
            description: "Path to the run directory (the one containing the 09_audit/ folder).",
          },
        },
        required: ["runDir"],
      },
    },
    {
      name: "swmm_rainfall_ensemble",
      description:
        "Generate a rainfall ensemble. Method 'perturbation' samples N noisy copies of an observed rainfall timeseries (gaussian_iid, multiplicative, autocorrelated, or intensity_scaling). Method 'idf' synthesises N design hyetographs (Chicago, Huff, or SCS Type II) by sampling IDF (a, b, c) parameters from their confidence intervals. Each realisation is written as a CSV; if a base INP is supplied, every realisation is patched and run through swmm5 and the summary aggregates peak flow + total volume.",
      inputSchema: {
        type: "object",
        properties: {
          method: {
            type: "string",
            enum: ["perturbation", "idf"],
            description: "Ensemble generation method.",
          },
          config: {
            type: "string",
            description: "Path to a JSON config (see skills/swmm-uncertainty/examples/rainfall_*_config.json).",
          },
          runRoot: {
            type: "string",
            description: "Output root. Summary lands at <runRoot>/09_audit/rainfall_ensemble_summary.json.",
          },
          baseInp: {
            type: "string",
            description: "If provided, each realisation is patched into a copy of this base INP and run through swmm5.",
          },
          seriesName: {
            type: "string",
            default: "TS_RAIN",
            description: "Name of the [TIMESERIES] block to replace.",
          },
          swmmNode: { type: "string", default: "O1" },
          seed: { type: "integer", default: 42 },
          dryRun: {
            type: "boolean",
            default: false,
            description: "Generate realisations + CSVs but skip swmm5.",
          },
        },
        required: ["method", "config", "runRoot"],
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
  if (name === "swmm_rainfall_ensemble") {
    const a = RainfallEnsembleArgs.parse(args);
    fs.mkdirSync(a.runRoot, { recursive: true });
    const pyArgs = [
      "--method", a.method,
      "--config", a.config,
      "--run-root", a.runRoot,
      "--series-name", a.seriesName,
      "--swmm-node", a.swmmNode,
      "--seed", String(a.seed),
    ];
    if (a.baseInp) pyArgs.push("--base-inp", a.baseInp);
    if (a.dryRun) pyArgs.push("--dry-run");
    const stdout = await runPy(rainfallEnsemblePy, pyArgs);
    return { content: [{ type: "text", text: stdout }] };
  }
  if (name === "swmm_uncertainty_source_decomposition") {
    const a = SourceDecompositionArgs.parse(args);
    // The script writes into <runDir>/09_audit/, which must already exist
    // for this to be a no-op for callers; we still mkdir defensively so a
    // brand-new run dir does not raise on the first audit invocation.
    fs.mkdirSync(path.join(a.runDir, "09_audit"), { recursive: true });
    const stdout = await runPy(sourceDecompositionPy, [a.runDir]);
    return { content: [{ type: "text", text: stdout }] };
  }
  throw new Error(`Unknown tool: ${name}`);
});

const transport = new StdioServerTransport();
await server.connect(transport);
