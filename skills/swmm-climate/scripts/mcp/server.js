#!/usr/bin/env node

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const scriptsDir = path.resolve(__dirname, '..');
const formatScript = path.join(scriptsDir, 'format_rainfall.py');
const raingageScript = path.join(scriptsDir, 'build_raingage_section.py');

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

const server = new McpServer({ name: 'swmm-climate-mcp', version: '0.1.0' });

server.tool(
  'format_rainfall',
  'Convert rainfall CSV into SWMM timeseries text + JSON metadata.',
  {
    inputCsvPath: z.string(),
    outputJsonPath: z.string(),
    outputTimeseriesPath: z.string(),
    seriesName: z.string().optional(),
    timestampColumn: z.string().optional(),
    valueColumn: z.string().optional(),
    timestampFormat: z.string().optional(),
  },
  async ({
    inputCsvPath,
    outputJsonPath,
    outputTimeseriesPath,
    seriesName,
    timestampColumn,
    valueColumn,
    timestampFormat,
  }) => {
    const args = [
      '--input', inputCsvPath,
      '--out-json', outputJsonPath,
      '--out-timeseries', outputTimeseriesPath,
    ];
    if (seriesName) args.push('--series-name', seriesName);
    if (timestampColumn) args.push('--timestamp-column', timestampColumn);
    if (valueColumn) args.push('--value-column', valueColumn);
    if (timestampFormat) args.push('--timestamp-format', timestampFormat);

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
    rainfallJsonPath: z.string().optional(),
    rainFormat: z.enum(['INTENSITY', 'VOLUME', 'CUMULATIVE']).optional(),
    intervalMin: z.number().int().positive().optional(),
    scf: z.number().positive().optional(),
  },
  async ({ outTextPath, outJsonPath, gageId, seriesName, rainfallJsonPath, rainFormat, intervalMin, scf }) => {
    const args = ['--out-text', outTextPath, '--out-json', outJsonPath];
    if (gageId) args.push('--gage-id', gageId);
    if (seriesName) args.push('--series-name', seriesName);
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
