/**
 * Shared prologue for the McpServer-family Python-tool servers (builder,
 * climate, experiment-audit, modeling-memory, network, params).
 *
 * Each of those six servers spawns a skill script under skills/<name>/scripts/
 * and expects stdout back; each one used to hand-roll its own copy of this
 * ~10-line spawnSync wrapper. Converged here per ADR-0006 D5. The five
 * low-level-SDK servers (calibration, gis, plot, runner, uncertainty) are
 * untouched -- they don't share this McpServer scaffolding.
 */

import { spawnSync } from 'node:child_process';

const PY = process.env.PYTHON || 'python3';

export function runPython(script, args) {
  const proc = spawnSync(PY, [script, ...args], { encoding: 'utf8' });
  if (proc.error) {
    throw new Error(proc.error.message);
  }
  if (proc.status !== 0) {
    throw new Error((proc.stderr || proc.stdout || `python failed: ${proc.status}`).trim());
  }
  return (proc.stdout || '').trim();
}
