#!/usr/bin/env node

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, '../..');
const scriptsDir = path.resolve(__dirname, '../../skills/swmm-builder/scripts');
const buildScript = path.join(scriptsDir, 'build_swmm_inp.py');

function resolvePython() {
  if (process.env.PYTHON) return process.env.PYTHON;
  const candidates = process.platform === 'win32'
    ? [path.join(repoRoot, '.venv', 'Scripts', 'python.exe')]
    : [path.join(repoRoot, '.venv', 'bin', 'python')];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

const pythonCmd = resolvePython();

function runPython(script, args) {
  const proc = spawnSync(pythonCmd, [script, ...args], { encoding: 'utf8' });
  if (proc.error) {
    throw new Error(proc.error.message);
  }
  if (proc.status !== 0) {
    throw new Error((proc.stderr || proc.stdout || `python failed: ${proc.status}`).trim());
  }
  return (proc.stdout || '').trim();
}

const server = new McpServer({ name: 'swmm-builder-mcp', version: '0.1.0' });

server.tool(
  'build_inp',
  'Assemble runnable SWMM INP + manifest from subcatchment/params/network/climate inputs.',
  {
    subcatchmentsCsvPath: z.string(),
    paramsJsonPath: z.string(),
    networkJsonPath: z.string(),
    outInpPath: z.string(),
    outManifestPath: z.string(),
    rainfallJsonPath: z.string().optional(),
    raingageJsonPath: z.string().optional(),
    timeseriesTextPath: z.string().optional(),
    configJsonPath: z.string().optional(),
    defaultGageId: z.string().optional(),
  },
  async ({
    subcatchmentsCsvPath,
    paramsJsonPath,
    networkJsonPath,
    outInpPath,
    outManifestPath,
    rainfallJsonPath,
    raingageJsonPath,
    timeseriesTextPath,
    configJsonPath,
    defaultGageId,
  }) => {
    const args = [
      '--subcatchments-csv', subcatchmentsCsvPath,
      '--params-json', paramsJsonPath,
      '--network-json', networkJsonPath,
      '--out-inp', outInpPath,
      '--out-manifest', outManifestPath,
    ];
    if (rainfallJsonPath) args.push('--rainfall-json', rainfallJsonPath);
    if (raingageJsonPath) args.push('--raingage-json', raingageJsonPath);
    if (timeseriesTextPath) args.push('--timeseries-text', timeseriesTextPath);
    if (configJsonPath) args.push('--config-json', configJsonPath);
    if (defaultGageId) args.push('--default-gage-id', defaultGageId);

    const out = runPython(buildScript, args);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `WROTE:${outInpPath}` },
        { type: 'text', text: `WROTE:${outManifestPath}` },
      ],
    };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
