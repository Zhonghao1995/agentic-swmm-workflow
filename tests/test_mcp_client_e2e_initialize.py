"""End-to-end check that the in-tree Python MCP client can complete the
JSON-RPC ``initialize`` handshake against a real MCP SDK server.

The Anthropic ``@modelcontextprotocol/sdk`` ``StdioServerTransport`` framing
is newline-delimited JSON. This test pins that contract: the test spawns a
real ``node mcp/swmm-builder/server.js`` subprocess and asserts that
``mcp_client.call_mcp("initialize", ...)`` returns within a short timeout
with a ``result`` field. It will hang/timeout if the client ever drifts
back to LSP ``Content-Length:`` framing.

Skips cleanly when ``node`` is not on PATH or the server's ``node_modules``
is missing so machines without the JavaScript toolchain can still run the
rest of the suite.
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


def test_initialize_handshake_completes_with_ndjson_framing() -> None:
    _require_node_environment()

    response = mcp_client.call_mcp(
        "node",
        [str(SERVER_JS)],
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "aiswmm-e2e-test", "version": "0.1"},
        },
        timeout=5,
    )

    assert "error" not in response, response
    assert "result" in response, response
    server_info = response["result"].get("serverInfo", {})
    assert server_info.get("name") == "swmm-builder-mcp"
