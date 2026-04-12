import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const scriptsDir = path.resolve(__dirname, '..');
const importScript = path.join(scriptsDir, 'network_import.py');
const qaScript = path.join(scriptsDir, 'network_qa.py');
const exportScript = path.join(scriptsDir, 'network_to_inp.py');

function runPython(script, args) {
  const proc = spawnSync('python3', [script, ...args], { encoding: 'utf8' });
  if (proc.status !== 0) {
    throw new Error((proc.stderr || proc.stdout || `python failed: ${proc.status}`).trim());
  }
  return proc.stdout;
}

const server = new McpServer({ name: 'swmm-network-mcp', version: '0.1.0' });

server.tool(
  'import_network',
  'Import conduit/junction/outfall data into the SWMM network JSON schema',
  {
    conduitsPath: z.string(),
    junctionsPath: z.string(),
    outfallsPath: z.string(),
    mappingPath: z.string(),
    outputPath: z.string().optional()
  },
  async ({ conduitsPath, junctionsPath, outfallsPath, mappingPath, outputPath }) => {
    const outJson = outputPath || path.join(os.tmpdir(), `swmm-network-${Date.now()}.json`);
    const out = runPython(importScript, [
      '--conduits', conduitsPath,
      '--junctions', junctionsPath,
      '--outfalls', outfallsPath,
      '--mapping', mappingPath,
      '--out', outJson
    ]);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `WROTE:${outJson}` }
      ]
    };
  }
);

server.tool(
  'qa',
  'Run QA checks on a SWMM network JSON model',
  { networkJsonPath: z.string() },
  async ({ networkJsonPath }) => {
    const out = runPython(qaScript, [networkJsonPath]);
    return { content: [{ type: 'text', text: out }] };
  }
);

server.tool(
  'export_inp',
  'Export a SWMM network JSON model to INP sections',
  { networkJsonPath: z.string() },
  async ({ networkJsonPath }) => {
    const tmp = path.join(os.tmpdir(), `swmm-network-${Date.now()}.inp`);
    const out = runPython(exportScript, [networkJsonPath, '--out', tmp]);
    return { content: [{ type: 'text', text: out }, { type: 'text', text: `WROTE:${tmp}` }] };
  }
);

server.tool(
  'summary',
  'Return a lightweight summary of a SWMM network JSON model',
  { networkJsonPath: z.string() },
  async ({ networkJsonPath }) => {
    const obj = JSON.parse(fs.readFileSync(networkJsonPath, 'utf8'));
    const summary = {
      junction_count: (obj.junctions || []).length,
      outfall_count: (obj.outfalls || []).length,
      conduit_count: (obj.conduits || []).length,
      total_conduit_length: (obj.conduits || []).reduce((acc, c) => acc + Number(c.length || 0), 0)
    };
    return { content: [{ type: 'text', text: JSON.stringify(summary, null, 2) }] };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
