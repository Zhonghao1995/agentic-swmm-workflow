#!/usr/bin/env node
/**
 * MCP server for the swmm-experiment-audit skill.
 * Wraps skills/swmm-experiment-audit/scripts/audit_run.py so the agent can
 * route audit calls through MCP instead of subprocess-Python.
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const scriptsDir = path.resolve(__dirname, '../../skills/swmm-experiment-audit/scripts');
const auditScript = path.join(scriptsDir, 'audit_run.py');

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

const server = new McpServer({ name: 'swmm-experiment-audit-mcp', version: '0.1.0' });

server.tool(
  'audit_run',
  'Audit a SWMM run directory and write deterministic provenance / comparison / note artifacts.',
  {
    runDir: z.string(),
    workflowMode: z.string().optional(),
    objective: z.string().optional(),
    compareTo: z.string().optional(),
    caseName: z.string().optional(),
    outProvenance: z.string().optional(),
    outComparison: z.string().optional(),
    outNote: z.string().optional(),
    outModelDiagnostics: z.string().optional(),
  },
  async ({
    runDir,
    workflowMode,
    objective,
    compareTo,
    caseName,
    outProvenance,
    outComparison,
    outNote,
    outModelDiagnostics,
  }) => {
    const args = ['--run-dir', runDir];
    if (workflowMode) args.push('--workflow-mode', workflowMode);
    if (objective) args.push('--objective', objective);
    if (compareTo) args.push('--compare-to', compareTo);
    if (caseName) args.push('--case-name', caseName);
    if (outProvenance) args.push('--out-provenance', outProvenance);
    if (outComparison) args.push('--out-comparison', outComparison);
    if (outNote) args.push('--out-note', outNote);
    if (outModelDiagnostics) args.push('--out-model-diagnostics', outModelDiagnostics);

    const out = runPython(auditScript, args);
    return {
      content: [
        { type: 'text', text: out },
        { type: 'text', text: `AUDITED:${runDir}` },
      ],
    };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
