"""End-to-end check that ``mcp_client.call_mcp`` surfaces a JSON-RPC
error body (not a timeout) when the server rejects a request.

We invoke a JSON-RPC method that is not part of the MCP protocol so the
server returns a real JSON-RPC ``-32601 Method not found`` error envelope
(rather than the ``isError: true`` content envelope that ``tools/call``
uses for unknown tools, which is a normal result and not a transport
error). The client must surface that envelope as
:class:`mcp_client.McpClientError`; if it were still speaking LSP
framing it would instead raise the generic ``"MCP response timed out."``
message.

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


def test_invalid_method_surfaces_jsonrpc_error_not_timeout() -> None:
    _require_node_environment()

    with pytest.raises(mcp_client.McpClientError) as excinfo:
        mcp_client.call_mcp(
            "node",
            [str(SERVER_JS)],
            "completely/invalid_method",
            {},
            timeout=5,
        )

    message = str(excinfo.value)
    assert "timed out" not in message.lower(), message
    # The MCP SDK returns a structured JSON-RPC error envelope; the client
    # serialises the error body, so the message must carry both "code" and
    # "message" keys from the real -32601 response.
    assert "code" in message, message
    assert "message" in message, message
