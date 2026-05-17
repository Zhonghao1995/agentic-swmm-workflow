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
const plotPy = path.resolve(__dirname, "../../skills/swmm-plot/scripts/plot_rain_runoff_si.py");

// Python interpreter — prefer launcher-supplied .venv (set via PYTHON env)
// so preheat hits the same site-packages the plot script will use.
const PY = process.env.PYTHON || "python3";

function runPy(args) {
  return new Promise((resolve, reject) => {
    const p = spawn(PY, [plotPy, ...args], { stdio: ["ignore", "pipe", "pipe"] });
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

// Fire-and-forget preheat: warm matplotlib font cache + swmmtoolbox bytecode
// at boot so the first plot tool call doesn't pay the ~30-90s cold-start tax
// inside a user-visible `you>` turn. See issue #109.
//
// Hard constraints:
//   - MUST NOT block the JSON-RPC initialize handshake (no await on this).
//   - MUST NOT write to stdout (stdio carries JSON-RPC framing). Both
//     stdout and stderr are piped and discarded; spawn errors are swallowed.
//   - If Python is missing or deps aren't installed, log a one-line warning
//     to stderr and let the user pay cold start on the first plot call —
//     the fallback warning in plot_rain_runoff_si.py covers that path.
function preheatPlotEnv() {
  const code = [
    "import matplotlib",
    "matplotlib.use('Agg')",
    "import matplotlib.pyplot as plt",
    "plt.figure(); plt.close()",
    "import swmmtoolbox",
  ].join("; ");
  try {
    const child = spawn(PY, ["-c", code], {
      stdio: ["ignore", "pipe", "pipe"],
      detached: false,
    });
    // Drain pipes so the child doesn't block on a full buffer.
    child.stdout?.on("data", () => {});
    child.stderr?.on("data", () => {});
    child.on("error", (err) => {
      process.stderr.write(`[swmm-plot] preheat skipped: ${err.message}\n`);
    });
    // Don't keep the event loop alive for the preheat.
    child.unref?.();
  } catch (err) {
    process.stderr.write(`[swmm-plot] preheat skipped: ${err?.message ?? err}\n`);
  }
}

// Issue #125 — agent-flow invariant.
// The `rainTs` / `node` defaults below are SELF-DOCUMENTING PLACEHOLDERS
// (`<rainfall-series-name>` / `<outfall-or-junction>`), not portability
// rot. They are unreachable in the agent-driven path:
//
//   agent goal
//     -> planner._extract_plot_choice (agentic_swmm/agent/planner.py)
//        which reads inspect_plot_options output and picks real names
//     -> tool_registry._plot_run_args (agentic_swmm/agent/tool_registry.py)
//        which forwards the explicit values into this MCP call
//     -> Args.parse() below sees the explicit values, never the defaults
//
// External MCP clients that omit these fields will see the placeholder
// strings reach the Python script, which fails fast with a clear error
// naming the missing flag. `nodeAttr` keeps its literal default because
// `Total_inflow` is a SWMM-universal attribute name, not watershed-specific.
//
// Regression guard: tests/test_plot_run_args_overrides_defaults.py.
const Args = z.object({
  inp: z.string(),
  out: z.string(),
  outPng: z.string(),
  rainTs: z.string().default("<rainfall-series-name>"),
  rainKind: z.enum(["intensity_mm_per_hr", "depth_mm_per_dt", "cumulative_depth_mm"]).default("depth_mm_per_dt"),
  dtMin: z.number().default(5),
  node: z.string().default("<outfall-or-junction>"),
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
            rainTs: { type: "string", default: "<rainfall-series-name>" },
            rainKind: { type: "string", enum: ["intensity_mm_per_hr", "depth_mm_per_dt", "cumulative_depth_mm"], default: "depth_mm_per_dt" },
            dtMin: { type: "number", default: 5 },
            node: { type: "string", default: "<outfall-or-junction>" },
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

// Kick off preheat before the transport handshake — it runs in parallel
// to JSON-RPC, never blocks it, and shaves ~89s -> ~5-15s off the first
// plot tool call. See issue #109.
preheatPlotEnv();

const transport = new StdioServerTransport();
await server.connect(transport);
