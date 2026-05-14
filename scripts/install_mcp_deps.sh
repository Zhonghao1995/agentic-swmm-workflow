#!/usr/bin/env bash
# install_mcp_deps.sh — run `npm install` in every mcp/<server>/ directory.
#
# Preflight for skills/swmm-end-to-end Mode 0 and other modes that touch
# MCP servers. node_modules/ is .gitignored (see .gitignore), so a fresh
# clone or a server added later requires this script to be runnable.
#
# Usage:
#   scripts/install_mcp_deps.sh           # install for all mcp/*/ servers
#   scripts/install_mcp_deps.sh swmm-gis  # install for a single server
#
# Exits non-zero if any install fails. Prints a per-server status line.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not on PATH" >&2
  exit 2
fi

if [ "$#" -gt 0 ]; then
  targets=()
  for name in "$@"; do
    targets+=("mcp/${name}")
  done
else
  targets=()
  for d in mcp/*/; do
    [ -f "${d}package.json" ] || continue
    targets+=("${d%/}")
  done
fi

if [ "${#targets[@]}" -eq 0 ]; then
  echo "no mcp/*/package.json found" >&2
  exit 1
fi

ok=0
fail=0
for dir in "${targets[@]}"; do
  if [ ! -f "${dir}/package.json" ]; then
    echo "SKIP  ${dir}  (no package.json)"
    continue
  fi
  printf "INSTALL %s ... " "${dir}"
  if (cd "${dir}" && npm install --silent --no-audit --no-fund) >/tmp/install_mcp_deps.log 2>&1; then
    pkg_count=$(ls "${dir}/node_modules" 2>/dev/null | wc -l | tr -d ' ')
    echo "ok (${pkg_count} pkgs)"
    ok=$((ok + 1))
  else
    echo "FAILED"
    sed 's/^/    /' /tmp/install_mcp_deps.log >&2
    fail=$((fail + 1))
  fi
done

rm -f /tmp/install_mcp_deps.log
echo "---"
echo "summary: ${ok} ok, ${fail} failed"
[ "$fail" -eq 0 ]
