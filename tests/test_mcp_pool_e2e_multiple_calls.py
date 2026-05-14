"""End-to-end: repeated ``list_tools`` on the same server reuses one
subprocess. This is the core win of the long-running pool — a chat turn
dispatching 5-10 calls now pays one node-startup cost, not five.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentic_swmm.agent.mcp_pool import MCPPool, ServerSpec


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO_ROOT / "mcp" / "swmm-builder"
SERVER_JS = SERVER_DIR / "server.js"


def _require_node_environment() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH; skipping MCP pool e2e test")
    if not SERVER_JS.exists():
        pytest.skip(f"missing MCP server: {SERVER_JS}")
    if not (SERVER_DIR / "node_modules").exists():
        pytest.skip("mcp/swmm-builder/node_modules is missing; run scripts/install_mcp_deps.sh")


def test_repeated_list_tools_reuses_one_node_subprocess() -> None:
    _require_node_environment()

    pool = MCPPool([ServerSpec(name="swmm-builder", command="node", args=[str(SERVER_JS)])])
    try:
        pool.list_tools("swmm-builder")
        first_proc = pool._handles["swmm-builder"].proc  # noqa: SLF001 — test introspection
        assert first_proc is not None
        first_pid = first_proc.pid

        for _ in range(2):
            pool.list_tools("swmm-builder")
            proc = pool._handles["swmm-builder"].proc  # noqa: SLF001
            assert proc is not None
            assert proc.pid == first_pid, "pool must reuse the same node subprocess"
            assert proc.poll() is None, "subprocess must still be running between calls"
    finally:
        pool.shutdown()
