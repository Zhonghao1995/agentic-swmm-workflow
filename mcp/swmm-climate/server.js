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
const scriptsDir = path.resolve(__dirname, '../../skills/swmm-climate/scripts');
const formatScript = path.join(scriptsDir, 'format_rainfall.py');
const raingageScript = path.join(scriptsDir, 'build_raingage_section.py');

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

const server = new McpServer({ name: 'swmm-climate-mcp', version: '0.1.0' });

server.tool(
  'format_rainfall',
  'Convert rainfall CSV into SWMM timeseries text + JSON metadata.',
  {
    inputCsvPath: z.string(),
    additionalInputCsvPaths: z.array(z.string()).optional(),
    inputGlobPatterns: z.array(z.string()).optional(),
    outputJsonPath: z.string(),
    outputTimeseriesPath: z.string(),
    seriesName: z.string().optional(),
    seriesNameTemplate: z.string().optional(),
    timestampColumn: z.string().optional(),
    valueColumn: z.string().optional(),
    stationColumn: z.string().optional(),
    defaultStationId: z.string().optional(),
    timestampFormat: z.string().optional(),
    windowStart: z.string().optional(),
    windowEnd: z.string().optional(),
    valueUnits: z.string().optional(),
    unitPolicy: z.enum(['strict', 'convert_to_mm_per_hr']).optional(),
    timestampPolicy: z.enum(['strict', 'sort']).optional(),
  },
  async ({
    inputCsvPath,
    additionalInputCsvPaths,
    inputGlobPatterns,
    outputJsonPath,
    outputTimeseriesPath,
    seriesName,
    seriesNameTemplate,
    timestampColumn,
    valueColumn,
    stationColumn,
    defaultStationId,
    timestampFormat,
    windowStart,
    windowEnd,
    valueUnits,
    unitPolicy,
    timestampPolicy,
  }) => {
    const args = [
      '--out-json', outputJsonPath,
      '--out-timeseries', outputTimeseriesPath,
    ];
    const allInputs = [inputCsvPath, ...(additionalInputCsvPaths || [])];
    for (const csvPath of allInputs) {
      args.push('--input', csvPath);
    }
    for (const pattern of inputGlobPatterns || []) {
      args.push('--input-glob', pattern);
    }
    if (seriesName) args.push('--series-name', seriesName);
    if (seriesNameTemplate) args.push('--series-name-template', seriesNameTemplate);
    if (timestampColumn) args.push('--timestamp-column', timestampColumn);
    if (valueColumn) args.push('--value-column', valueColumn);
    if (stationColumn) args.push('--station-column', stationColumn);
    if (defaultStationId) args.push('--default-station-id', defaultStationId);
    if (timestampFormat) args.push('--timestamp-format', timestampFormat);
    if (windowStart) args.push('--window-start', windowStart);
    if (windowEnd) args.push('--window-end', windowEnd);
    if (valueUnits) args.push('--value-units', valueUnits);
    if (unitPolicy) args.push('--unit-policy', unitPolicy);
    if (timestampPolicy) args.push('--timestamp-policy', timestampPolicy);

    const out = runPython(formatScript, args);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `WROTE:${outputJsonPath}` },
        { type: 'text', text: `WROTE:${outputTimeseriesPath}` },
      ],
    };
  }
);

server.tool(
  'build_raingage_section',
  'Build SWMM [RAINGAGES] helper snippet tied to a rainfall timeseries name.',
  {
    outTextPath: z.string(),
    outJsonPath: z.string(),
    gageId: z.string().optional(),
    seriesName: z.string().optional(),
    stationId: z.string().optional(),
    rainfallJsonPath: z.string().optional(),
    rainFormat: z.enum(['INTENSITY', 'VOLUME', 'CUMULATIVE']).optional(),
    intervalMin: z.number().int().positive().optional(),
    scf: z.number().positive().optional(),
  },
  async ({ outTextPath, outJsonPath, gageId, seriesName, stationId, rainfallJsonPath, rainFormat, intervalMin, scf }) => {
    const args = ['--out-text', outTextPath, '--out-json', outJsonPath];
    if (gageId) args.push('--gage-id', gageId);
    if (seriesName) args.push('--series-name', seriesName);
    if (stationId) args.push('--station-id', stationId);
    if (rainfallJsonPath) args.push('--rainfall-json', rainfallJsonPath);
    if (rainFormat) args.push('--rain-format', rainFormat);
    if (intervalMin) args.push('--interval-min', String(intervalMin));
    if (scf) args.push('--scf', String(scf));

    const out = runPython(raingageScript, args);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `WROTE:${outTextPath}` },
        { type: 'text', text: `WROTE:${outJsonPath}` },
      ],
    };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
