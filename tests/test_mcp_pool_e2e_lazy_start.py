"""End-to-end: ``MCPPool`` lazy-starts a real ``node mcp/swmm-builder/server.js``
on first ``list_tools`` and surfaces the ``build_inp`` tool.

Skips cleanly when Node or ``mcp/swmm-builder/node_modules`` is missing so
that Python-only environments still see the rest of the test suite green.
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
        pytest.skip(
            "mcp/swmm-builder/node_modules is missing; run "
            "scripts/install_mcp_deps.sh (or aiswmm setup --install-mcp)"
        )


def test_pool_lazy_starts_real_swmm_builder_and_lists_build_inp() -> None:
    _require_node_environment()

    pool = MCPPool([ServerSpec(name="swmm-builder", command="node", args=[str(SERVER_JS)])])
    # Construction must not spawn anything.
    assert pool.list_servers() == ["swmm-builder"]

    try:
        tools = pool.list_tools("swmm-builder")
    finally:
        pool.shutdown()

    names = {tool.get("name") for tool in tools}
    assert "build_inp" in names, names
