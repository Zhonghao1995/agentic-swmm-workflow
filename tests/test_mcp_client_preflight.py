"""Preflight checks in ``mcp_client.call_mcp``.

Without the preflight the user would see either a ``FileNotFoundError``
from ``subprocess.Popen`` (no ``node`` on PATH) or a 20 s timeout (server
crashed because its ``node_modules`` are missing). Both are bad UX; the
preflight raises :class:`McpClientError` with a one-line fix.
"""

from __future__ import annotations

import sys
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


def test_launcher_form_reports_missing_node_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live finding F-122 (2026-09-03, S55): the registry launches servers as
    ``node scripts/run_mcp_server.mjs <name>``; the preflight only knew the
    ``server.js`` form, so a missing node_modules surfaced as "MCP process
    ended before sending a complete line"."""
    server_dir = tmp_path / "mcp" / "swmm-uncertainty"
    server_dir.mkdir(parents=True)
    (server_dir / "package.json").write_text("{}\n", encoding="utf-8")
    launcher = tmp_path / "scripts" / "run_mcp_server.mjs"
    launcher.parent.mkdir()
    launcher.write_text("// noop\n", encoding="utf-8")
    monkeypatch.setattr(mcp_client, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(mcp_client, "resource_root", lambda: tmp_path)
    with pytest.raises(mcp_client.McpClientError) as excinfo:
        mcp_client.call_mcp("/usr/bin/env", ["node", str(launcher), "swmm-uncertainty"], "initialize", {}, timeout=1)
    message = str(excinfo.value)
    assert "swmm-uncertainty" in message, message
    assert "install_mcp_deps.sh swmm-uncertainty" in message, message


def test_launcher_form_passes_when_node_modules_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server_dir = tmp_path / "mcp" / "swmm-uncertainty"
    (server_dir / "node_modules").mkdir(parents=True)
    (server_dir / "package.json").write_text("{}\n", encoding="utf-8")
    launcher = tmp_path / "scripts" / "run_mcp_server.mjs"
    launcher.parent.mkdir()
    launcher.write_text("// noop\n", encoding="utf-8")
    monkeypatch.setattr(mcp_client, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(mcp_client, "resource_root", lambda: tmp_path)
    mcp_client._preflight("/opt/homebrew/bin/node", [str(launcher), "swmm-uncertainty"])  # no exception


def test_a_server_that_dies_early_reports_its_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_client, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(mcp_client, "resource_root", lambda: tmp_path)
    script = "import sys; sys.stderr.write('node:internal/x\\n  throw new ERR_MODULE_NOT_FOUND\\nError [ERR_MODULE_NOT_FOUND]: Cannot find package zod imported from server.js\\n'); sys.exit(1)"
    with pytest.raises(mcp_client.McpClientError) as excinfo:
        mcp_client.call_mcp(sys.executable, ["-c", script], "initialize", {}, timeout=10)
    message = str(excinfo.value)
    assert "ended before sending a complete line" in message, message
    assert "Cannot find package zod" in message, message


def test_a_silent_early_death_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_client, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(mcp_client, "resource_root", lambda: tmp_path)
    with pytest.raises(mcp_client.McpClientError) as excinfo:
        mcp_client.call_mcp(sys.executable, ["-c", "import sys; sys.exit(1)"], "initialize", {}, timeout=10)
    assert "wrote nothing to stderr" in str(excinfo.value)
