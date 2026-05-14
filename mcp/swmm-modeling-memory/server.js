#!/usr/bin/env node
/**
 * MCP server for the swmm-modeling-memory skill.
 * Wraps skills/swmm-modeling-memory/scripts/summarize_memory.py so the
 * agent can route memory-summary calls through MCP.
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const scriptsDir = path.resolve(__dirname, '../../skills/swmm-modeling-memory/scripts');
const summariseScript = path.join(scriptsDir, 'summarize_memory.py');

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

const server = new McpServer({ name: 'swmm-modeling-memory-mcp', version: '0.1.0' });

server.tool(
  'summarize_memory',
  'Summarize audited SWMM run directories into modeling-memory outputs.',
  {
    runsDir: z.string(),
    outDir: z.string(),
    obsidianDir: z.string().optional(),
  },
  async ({ runsDir, outDir, obsidianDir }) => {
    const args = ['--runs-dir', runsDir, '--out-dir', outDir];
    if (obsidianDir) args.push('--obsidian-dir', obsidianDir);
    const out = runPython(summariseScript, args);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `WROTE:${outDir}` },
      ],
    };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
