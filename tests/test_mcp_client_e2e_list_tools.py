"""End-to-end check that ``mcp_client.list_tools`` returns a non-empty
tool descriptor list against a real MCP SDK server (``swmm-builder``).

This test guarantees that the wire format negotiated by ``initialize``
also carries through the subsequent ``tools/list`` request. It will fail
or time out if the client ever drifts back to LSP ``Content-Length:``
framing while the server speaks NDJSON.

Skips cleanly when ``node`` is not on PATH or the server's ``node_modules``
is missing.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentic_swmm.agent import mcp_client


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO_ROOT / "mcp" / "swmm-builder"
SERVER_JS = SERVER_DIR / "server.js"


def _require_node_environment() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH; skipping MCP stdio e2e test")
    if not SERVER_JS.exists():
        pytest.skip(f"missing MCP server: {SERVER_JS}")
    if not (SERVER_DIR / "node_modules").exists():
        pytest.skip(
            "mcp/swmm-builder/node_modules is missing; run "
            "scripts/install_mcp_deps.sh (or aiswmm setup --install-mcp)"
        )


def test_list_tools_returns_build_inp_descriptor() -> None:
    _require_node_environment()

    tools = mcp_client.list_tools("node", [str(SERVER_JS)], timeout=5)

    assert isinstance(tools, list)
    assert tools, "expected at least one MCP tool descriptor"
    names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
    assert "build_inp" in names, names
