"""Behavioural tests for ``aiswmm mcp coverage``.

Per PRD-X Done Criteria: ``aiswmm mcp coverage`` exits 0 when every
subprocess-Python ToolSpec is exposed through some MCP server, exits 1
with a clear table when any binding is MISSING.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", "mcp", "coverage", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_coverage_subcommand_exits_zero_when_all_ok() -> None:
    """The repository's bindings must all be OK (otherwise the lock-in test
    already fired). The CLI prints a human-readable table and exits 0."""

    proc = _run_cli([])
    assert proc.returncode == 0, proc.stderr or proc.stdout
    # Header columns are present and at least one known binding is listed.
    assert "ToolSpec" in proc.stdout
    assert "MCP server" in proc.stdout
    assert "audit_run" in proc.stdout
    assert "summarize_memory" in proc.stdout


def test_coverage_subcommand_supports_json_output() -> None:
    """``--json`` returns the same matrix as a parseable list of records."""

    proc = _run_cli(["--json"])
    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)
    assert isinstance(payload, list)
    assert all(set(entry.keys()) >= {"tool_spec_name", "mcp_server", "mcp_tool_name", "status"} for entry in payload)
    assert {entry["tool_spec_name"] for entry in payload} >= {"audit_run", "build_inp", "summarize_memory"}
    assert all(entry["status"] == "OK" for entry in payload)
