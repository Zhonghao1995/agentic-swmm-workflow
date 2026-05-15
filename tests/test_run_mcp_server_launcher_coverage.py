"""Regression: ``scripts/run_mcp_server.mjs`` must know every MCP server.

Before this regression, the launcher's ``servers`` map shipped without
entries for ``swmm-experiment-audit`` / ``swmm-modeling-memory`` /
``swmm-uncertainty`` while ``agentic_swmm.runtime.registry.MCP_SERVERS``
already advertised them. The agent's MCP pool spawned
``node scripts/run_mcp_server.mjs swmm-experiment-audit``, the launcher
printed ``Usage:`` to stderr and exited 2 *before* the JSON-RPC handshake,
and the client surfaced ``MCP transport failed: MCP process ended before
sending a complete line.`` This test would have caught the drift.

Two-pronged invariant:
1. Every server in ``MCP_SERVERS`` appears in the launcher's ``servers``
   map (drift detector — static text check, no node required).
2. The bare launcher process, invoked with the audit server name, does
   not exit non-zero before emitting a JSON-RPC ``initialize`` response
   on stdout (live spawn check — gated on node + node_modules).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentic_swmm.runtime import registry
from agentic_swmm.utils.paths import repo_root

LAUNCHER = repo_root() / "scripts" / "run_mcp_server.mjs"


def test_launcher_servers_map_covers_every_registered_mcp_server() -> None:
    """Drift detector: launcher must list every server in ``MCP_SERVERS``."""
    text = LAUNCHER.read_text(encoding="utf-8")
    missing = [name for name in registry.MCP_SERVERS if f'"{name}"' not in text]
    assert not missing, (
        f"scripts/run_mcp_server.mjs is missing entries for {missing}; "
        "the agent will spawn the launcher which immediately prints "
        "'Usage:' to stderr and exits 2, surfacing as "
        "'MCP transport failed: MCP process ended before sending a complete line.' "
        "Add the missing names to the `servers` map in the launcher."
    )


def _node_available() -> bool:
    return shutil.which("node") is not None


def _audit_server_installed() -> bool:
    return (repo_root() / "mcp" / "swmm-experiment-audit" / "node_modules").exists()


@pytest.mark.skipif(
    not _node_available() or not _audit_server_installed(),
    reason="node or mcp/swmm-experiment-audit/node_modules not installed",
)
def test_launcher_audit_server_first_stdout_line_is_jsonrpc() -> None:
    """Live: bare launcher for the audit MCP must emit valid JSON-RPC."""
    proc = subprocess.Popen(
        ["node", str(LAUNCHER), "swmm-experiment-audit"],
        cwd=repo_root(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ},
    )
    try:
        request = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "regression-test", "version": "0.1"},
                    },
                }
            )
            + "\n"
        )
        assert proc.stdin is not None
        proc.stdin.write(request.encode("utf-8"))
        proc.stdin.flush()
        assert proc.stdout is not None
        first_line = proc.stdout.readline()
        assert first_line, (
            "launcher exited before emitting a JSON-RPC frame; "
            "this is the exact 'MCP process ended before sending a complete line' "
            "failure mode reported on the agent path."
        )
        parsed = json.loads(first_line.decode("utf-8"))
        assert parsed.get("jsonrpc") == "2.0"
        assert parsed.get("id") == 1
        assert "result" in parsed, parsed
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
