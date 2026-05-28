"""Regression: ``scripts/run_mcp_server.mjs`` must know every MCP server.

Before this regression, the launcher's ``servers`` map shipped without
entries for ``swmm-experiment-audit`` / ``swmm-modeling-memory`` /
``swmm-uncertainty`` while ``agentic_swmm.runtime.registry.MCP_SERVERS``
already advertised them. The agent's MCP pool spawned
``node scripts/run_mcp_server.mjs swmm-experiment-audit``, the launcher
printed ``Usage:`` to stderr and exited 2 *before* the JSON-RPC handshake,
and the client surfaced ``MCP transport failed: MCP process ended before
sending a complete line.`` This test would have caught the drift.

Three-pronged invariant:
1. Every server in ``MCP_SERVERS`` appears in the launcher's ``servers``
   map (drift detector — static text check, no node required).
2. The bare launcher process, invoked with the audit server name, does
   not exit non-zero before emitting a JSON-RPC ``initialize`` response
   on stdout (live spawn check — gated on node + node_modules).
3. A pre-existing but unusable ``.venv/bin/python`` (e.g. a zero-byte
   stub left over from a half-finished fixture) must NOT poison
   ``env.PYTHON`` for the spawned MCP server, otherwise the next
   ``spawn(PY, ...)`` from the server returns ``spawn ENOEXEC`` and
   surfaces as ``MCP transport failed: spawn ENOEXEC``.
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


@pytest.mark.skipif(not _node_available(), reason="node not installed")
def test_launcher_rejects_unusable_venv_python_stub(tmp_path: Path) -> None:
    """A zero-byte ``.venv/bin/python`` left in the repo (test fixture
    leak, partial pip install, stray subagent worktree) must not be
    picked up by the launcher's Python discovery. If it were, the
    spawned MCP server's ``spawn(PY, [runner, ...])`` returns
    ``spawn ENOEXEC`` because the kernel cannot recognise an empty
    file as an executable, surfacing all the way back to the agent
    as ``MCP transport failed: spawn ENOEXEC``.

    The test materialises that broken fixture inside the real repo,
    runs the launcher with ``PYTHON`` unset, and asserts the child
    Node process either rejects the stub (clean) or never sets it
    as ``env.PYTHON``. We probe by asking the launched MCP server
    to surface what ``process.env.PYTHON`` it saw — easier than
    inspecting the spawn syscall — via a one-shot stderr line the
    server emits on its ``initialize`` handler. Since editing the
    server to add that probe would be invasive, we instead spawn the
    launcher with a side-channel ``AISWMM_LAUNCHER_PYTHON_PROBE=1``
    that the launcher itself honours by printing the resolved
    ``env.PYTHON`` to stderr and exiting 0 before forking the
    server. Without the probe env var, the launcher behaves
    identically to before.
    """
    venv_bin = repo_root() / ".venv" / "bin"
    stub = venv_bin / "python"
    pre_existing = stub.exists()
    pre_existing_bytes = stub.read_bytes() if pre_existing else None
    venv_bin.mkdir(parents=True, exist_ok=True)
    stub.write_bytes(b"")
    stub.chmod(0o755)
    try:
        env = {k: v for k, v in os.environ.items() if k != "PYTHON"}
        env["AISWMM_LAUNCHER_PYTHON_PROBE"] = "1"
        proc = subprocess.run(
            ["node", str(LAUNCHER), "swmm-runner"],
            cwd=repo_root(),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # The probe should emit a single line on stderr describing the
        # resolved interpreter. The launcher must NOT have selected the
        # empty stub.
        assert proc.returncode == 0, (
            f"launcher probe failed (rc={proc.returncode}):\n"
            f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )
        probe_line = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ""
        assert probe_line.startswith("PYTHON="), (
            f"launcher probe did not emit a PYTHON= line; stderr={proc.stderr!r}"
        )
        resolved = probe_line.split("=", 1)[1]
        # The launcher may legitimately resolve to '' (no candidate) — that's
        # fine; the server-side fallback to ``python3`` then takes over. What
        # it MUST NOT do is hand back the empty stub.
        assert resolved != str(stub), (
            "launcher picked the zero-byte stub at .venv/bin/python — this is "
            "the spawn-ENOEXEC bug. Tighten the candidate filter to reject "
            "empty / non-executable files."
        )
    finally:
        try:
            stub.unlink()
        except FileNotFoundError:
            pass
        if pre_existing and pre_existing_bytes is not None:
            stub.write_bytes(pre_existing_bytes)
            stub.chmod(0o755)
        else:
            # Clean up the .venv/bin we created if the original repo had no .venv.
            try:
                venv_bin.rmdir()
                venv_bin.parent.rmdir()
            except OSError:
                pass
