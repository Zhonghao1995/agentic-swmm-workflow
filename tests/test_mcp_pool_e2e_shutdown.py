"""End-to-end: ``MCPPool.shutdown`` terminates any started node child.

Per PRD-X Done Criteria: "aiswmm clean exit terminates all started MCP
child processes (no orphan node after aiswmm quits)." We assert here that
once ``shutdown`` returns, the started subprocess has exited (``returncode``
is set, not ``None``).
"""

from __future__ import annotations

import shutil
import time
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


def test_shutdown_terminates_started_node_child() -> None:
    _require_node_environment()

    pool = MCPPool([ServerSpec(name="swmm-builder", command="node", args=[str(SERVER_JS)])])
    pool.list_tools("swmm-builder")
    proc = pool._handles["swmm-builder"].proc  # noqa: SLF001 — test introspection
    assert proc is not None
    assert proc.poll() is None

    pool.shutdown()

    # Give the OS a moment to finalise the return code, then assert exit.
    deadline = time.monotonic() + 3
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert proc.returncode is not None, "node subprocess did not exit after shutdown"

    # Idempotent: calling shutdown again must not raise and must not error.
    pool.shutdown()
