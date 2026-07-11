#!/usr/bin/env node

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { runPython } from '../_lib/python-tool-server.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const scriptsDir = path.resolve(__dirname, '../../skills/swmm-climate/scripts');
const formatScript = path.join(scriptsDir, 'format_rainfall.py');
const raingageScript = path.join(scriptsDir, 'build_raingage_section.py');
const designStormScript = path.join(scriptsDir, 'design_storm.py');

const server = new McpServer({ name: 'swmm-climate-mcp', version: '0.1.0' });

server.tool(
  'format_rainfall',
  'Convert rainfall CSV or SWMM .dat into SWMM timeseries text + JSON metadata.',
  {
    inputCsvPath: z.string().optional(),
    additionalInputCsvPaths: z.array(z.string()).optional(),
    inputGlobPatterns: z.array(z.string()).optional(),
    inputDatPaths: z.array(z.string()).optional(),
    datValueUnits: z.string().optional(),
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
    inputDatPaths,
    datValueUnits,
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
    const datInputs = inputDatPaths || [];
    if (datInputs.length === 0 && !inputCsvPath && (!inputGlobPatterns || inputGlobPatterns.length === 0)) {
      throw new Error('format_rainfall requires inputCsvPath, inputGlobPatterns, or inputDatPaths');
    }
    if (datInputs.length > 0 && (inputCsvPath || (inputGlobPatterns || []).length > 0 || (additionalInputCsvPaths || []).length > 0)) {
      throw new Error('format_rainfall: inputDatPaths cannot be combined with CSV inputs');
    }
    const args = [
      '--out-json', outputJsonPath,
      '--out-timeseries', outputTimeseriesPath,
    ];
    if (datInputs.length > 0) {
      for (const datPath of datInputs) {
        args.push('--input-dat', datPath);
      }
      if (datValueUnits) args.push('--dat-value-units', datValueUnits);
    } else {
      const allInputs = [inputCsvPath, ...(additionalInputCsvPaths || [])].filter(Boolean);
      for (const csvPath of allInputs) {
        args.push('--input', csvPath);
      }
      for (const pattern of inputGlobPatterns || []) {
        args.push('--input-glob', pattern);
      }
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

server.tool(
  'generate_design_storm',
  'Synthesise a design-storm hyetograph (Chicago/Keifer-Chu or alternating-block) from return period and IDF coefficients when no measured rainfall exists. Writes SWMM [TIMESERIES] text and metadata JSON matching format_rainfall output contract.',
  {
    // Required
    method: z.enum(['chicago', 'alternating_block']),
    duration: z.number().positive(),
    outJson: z.string(),
    outTimeseries: z.string(),
    // Chicago IDF form
    form: z.enum(['CN', 'generic']).optional(),
    // CN form coefficients
    a1: z.number().optional(),
    cCoeff: z.number().optional(),
    b: z.number().optional(),
    n: z.number().optional(),
    // Generic form coefficients
    aCoeff: z.number().optional(),
    cExp: z.number().optional(),
    // Alternating-block IDF table
    idfCsv: z.string().optional(),
    idfJson: z.string().optional(),
    // Storm parameters
    returnPeriod: z.number().positive().optional(),
    dt: z.number().positive().optional(),
    r: z.number().positive().optional(),
    // Output
    seriesName: z.string().optional(),
  },
  async ({
    method,
    duration,
    outJson,
    outTimeseries,
    form,
    a1,
    cCoeff,
    b,
    n,
    aCoeff,
    cExp,
    idfCsv,
    idfJson,
    returnPeriod,
    dt,
    r,
    seriesName,
  }) => {
    const args = [
      '--method', method,
      '--duration', String(duration),
      '--out-json', outJson,
      '--out-timeseries', outTimeseries,
    ];
    if (form) args.push('--form', form);
    if (a1 != null) args.push('--a1', String(a1));
    if (cCoeff != null) args.push('--C', String(cCoeff));
    if (b != null) args.push('--b', String(b));
    if (n != null) args.push('--n', String(n));
    if (aCoeff != null) args.push('--a-coeff', String(aCoeff));
    if (cExp != null) args.push('--c-exp', String(cExp));
    if (idfCsv) args.push('--idf-csv', idfCsv);
    if (idfJson) args.push('--idf-json', idfJson);
    if (returnPeriod != null) args.push('--return-period', String(returnPeriod));
    if (dt != null) args.push('--dt', String(dt));
    if (r != null) args.push('--r', String(r));
    if (seriesName) args.push('--series-name', seriesName);

    const out = runPython(designStormScript, args);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `WROTE:${outJson}` },
        { type: 'text', text: `WROTE:${outTimeseries}` },
      ],
    };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
