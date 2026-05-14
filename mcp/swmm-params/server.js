#!/usr/bin/env node

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const scriptsDir = path.resolve(__dirname, '../../skills/swmm-params/scripts');
const landuseScript = path.join(scriptsDir, 'landuse_to_swmm_params.py');
const soilScript = path.join(scriptsDir, 'soil_to_greenampt.py');
const mergeScript = path.join(scriptsDir, 'merge_swmm_params.py');

function runPython(script, args) {
  const proc = spawnSync('python3', [script, ...args], { encoding: 'utf8' });
  if (proc.error) {
    throw new Error(proc.error.message);
  }
  if (proc.status !== 0) {
    throw new Error((proc.stderr || proc.stdout || `python failed: ${proc.status}`).trim());
  }
  return (proc.stdout || '').trim();
}

const server = new McpServer({ name: 'swmm-params-mcp', version: '0.1.0' });

server.tool(
  'map_landuse',
  'Map subcatchment land use CSV to SWMM subcatchment/subarea parameter JSON.',
  {
    inputCsvPath: z.string(),
    lookupCsvPath: z.string().optional(),
    outputPath: z.string(),
  },
  async ({ inputCsvPath, lookupCsvPath, outputPath }) => {
    const args = ['--input', inputCsvPath, '--output', outputPath];
    if (lookupCsvPath) {
      args.push('--lookup', lookupCsvPath);
    }
    const out = runPython(landuseScript, args);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `WROTE:${outputPath}` },
      ],
    };
  }
);

server.tool(
  'map_soil',
  'Map subcatchment soil CSV to SWMM Green-Ampt infiltration parameter JSON.',
  {
    inputCsvPath: z.string(),
    lookupCsvPath: z.string().optional(),
    outputPath: z.string(),
  },
  async ({ inputCsvPath, lookupCsvPath, outputPath }) => {
    const args = ['--input', inputCsvPath, '--output', outputPath];
    if (lookupCsvPath) {
      args.push('--lookup', lookupCsvPath);
    }
    const out = runPython(soilScript, args);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `WROTE:${outputPath}` },
      ],
    };
  }
);

server.tool(
  'merge_params',
  'Merge land use and soil JSON outputs into one SWMM params payload.',
  {
    landuseJsonPath: z.string(),
    soilJsonPath: z.string(),
    outputPath: z.string(),
  },
  async ({ landuseJsonPath, soilJsonPath, outputPath }) => {
    const out = runPython(mergeScript, [
      '--landuse-json',
      landuseJsonPath,
      '--soil-json',
      soilJsonPath,
      '--output',
      outputPath,
    ]);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `WROTE:${outputPath}` },
      ],
    };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
