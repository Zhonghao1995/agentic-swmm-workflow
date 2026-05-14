"""Preflight checks in ``mcp_client.call_mcp``.

Without the preflight the user would see either a ``FileNotFoundError``
from ``subprocess.Popen`` (no ``node`` on PATH) or a 20 s timeout (server
crashed because its ``node_modules`` are missing). Both are bad UX; the
preflight raises :class:`McpClientError` with a one-line fix.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from agentic_swmm.agent import mcp_client


def test_missing_node_modules_raises_friendly_error(tmp_path: Path) -> None:
    server_dir = tmp_path / "mcp" / "swmm-builder"
    server_dir.mkdir(parents=True)
    server_js = server_dir / "server.js"
    server_js.write_text("// noop\n", encoding="utf-8")
    # no node_modules dir intentionally

    with pytest.raises(mcp_client.McpClientError) as excinfo:
        mcp_client.call_mcp(
            "node",
            [str(server_js)],
            "initialize",
            {},
            timeout=1,
        )

    message = str(excinfo.value)
    assert "swmm-builder" in message, message
    assert "node_modules" in message, message
    # Recovery path users will follow
    assert "install_mcp_deps.sh" in message or "aiswmm setup" in message, message


def test_missing_node_on_path_raises_friendly_error(tmp_path: Path) -> None:
    server_dir = tmp_path / "mcp" / "swmm-builder"
    server_dir.mkdir(parents=True)
    (server_dir / "node_modules").mkdir()
    server_js = server_dir / "server.js"
    server_js.write_text("// noop\n", encoding="utf-8")

    # Pretend node is not on PATH; node_modules exists so the node_modules
    # branch cannot fire first.
    with mock.patch.object(mcp_client.shutil, "which", return_value=None):
        with pytest.raises(mcp_client.McpClientError) as excinfo:
            mcp_client.call_mcp(
                "node",
                [str(server_js)],
                "initialize",
                {},
                timeout=1,
            )

    message = str(excinfo.value)
    assert "node" in message.lower(), message
    assert "path" in message.lower() or "node.js" in message.lower(), message
