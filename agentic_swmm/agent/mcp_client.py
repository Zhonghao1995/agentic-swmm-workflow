from __future__ import annotations

import json
import select
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from agentic_swmm.utils.paths import repo_root
from agentic_swmm.utils.subprocess_runner import runtime_env


class McpClientError(RuntimeError):
    pass


def call_mcp(command: str, args: list[str], method: str, params: dict[str, Any] | None = None, *, timeout: int = 20) -> dict[str, Any]:
    _preflight(command, args)
    proc = subprocess.Popen(
        [command, *args],
        cwd=repo_root(),
        env=runtime_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stderr_chunks: list[bytes] = []

    def _read_stderr() -> None:
        if proc.stderr is None:
            return
        stderr_chunks.append(proc.stderr.read())

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "aiswmm-agent", "version": "0.1"}}})
        initialized = _read(proc, timeout=timeout)
        if "error" in initialized:
            raise McpClientError(json.dumps(initialized["error"], sort_keys=True))
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}})
        response = _read(proc, timeout=timeout)
        if "error" in response:
            raise McpClientError(json.dumps(response["error"], sort_keys=True))
        return response
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


def list_tools(command: str, args: list[str], *, timeout: int = 20) -> list[dict[str, Any]]:
    routed = _route_through_pool(command, args)
    if routed is not None:
        pool, server_name = routed
        return pool.list_tools(server_name, timeout=timeout)
    response = call_mcp(command, args, "tools/list", {}, timeout=timeout)
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    tools = result.get("tools", [])
    return tools if isinstance(tools, list) else []


def call_tool(command: str, args: list[str], tool_name: str, arguments: dict[str, Any], *, timeout: int = 60) -> dict[str, Any]:
    routed = _route_through_pool(command, args)
    if routed is not None:
        pool, server_name = routed
        return pool.call_tool(server_name, tool_name, arguments, timeout=timeout)
    response = call_mcp(command, args, "tools/call", {"name": tool_name, "arguments": arguments}, timeout=timeout)
    result = response.get("result")
    return result if isinstance(result, dict) else {"result": result}


def _route_through_pool(command: str, args: list[str]) -> tuple[Any, str] | None:
    """If a session pool is bound and one of its specs matches ``(command,
    args)``, return ``(pool, server_name)`` so callers can route through it.
    Returns ``None`` otherwise — caller falls back to spawn-per-call.

    Matching policy: command equality + args equality. We treat ``args`` as
    a canonical list (e.g. ``["mcp/swmm-builder/server.js"]``); the runtime
    registry uses absolute or repo-relative paths consistently within one
    session, so equality is enough. Defensive against silent mismatches.
    """

    # Late import: ``mcp_pool`` imports from this module, so we cannot do
    # this at module top level without creating a circular import.
    from agentic_swmm.agent import mcp_pool

    pool = mcp_pool.session_pool()
    if pool is None:
        return None
    handles = getattr(pool, "_handles", None)
    if not isinstance(handles, dict):
        return None
    for name, handle in handles.items():
        spec = getattr(handle, "spec", None)
        if spec is None:
            # Test doubles may stash the spec on a `specs` list instead of
            # using ``MCPServerHandle``; fall back to that shape.
            continue
        if spec.command == command and list(spec.args) == list(args):
            return pool, name
    # Test doubles: a pool that exposes ``specs`` directly (see
    # ``tests/test_mcp_client_pool_routing.py``).
    for spec in getattr(pool, "specs", []):
        if getattr(spec, "command", None) == command and list(getattr(spec, "args", [])) == list(args):
            return pool, spec.name
    return None


def _preflight(command: str, args: list[str]) -> None:
    """Surface a friendly error before ``subprocess.Popen`` when the toolchain
    or installed dependencies are missing. Without this the caller would see
    either a 20 s timeout (no response on the pipe) or a cryptic
    ``FileNotFoundError`` from ``Popen`` itself.
    """
    if command == "node" and shutil.which("node") is None:
        raise McpClientError(
            "node is not on PATH; MCP servers require Node.js. "
            "Install Node 18+ (or run: aiswmm setup --install-mcp)."
        )
    for arg in args:
        if not isinstance(arg, str):
            continue
        if not arg.endswith("server.js"):
            continue
        server_path = Path(arg)
        if not server_path.is_absolute():
            server_path = repo_root() / server_path
        server_dir = server_path.parent
        node_modules = server_dir / "node_modules"
        if node_modules.exists():
            continue
        server_name = server_dir.name or str(server_dir)
        raise McpClientError(
            f"MCP server {server_name} has no node_modules. "
            "Run: bash scripts/install_mcp_deps.sh (or aiswmm setup --install-mcp)"
        )


def _send(proc: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise McpClientError("MCP process stdin is unavailable.")
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    proc.stdin.write(data + b"\n")
    proc.stdin.flush()


def _read(proc: subprocess.Popen[bytes], *, timeout: int) -> dict[str, Any]:
    if proc.stdout is None:
        raise McpClientError("MCP process stdout is unavailable.")
    line = _readline(proc.stdout, timeout=timeout)
    parsed = json.loads(line.decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _readline(stream: Any, *, timeout: int) -> bytes:
    deadline = time.monotonic() + timeout
    data = b""
    while not data.endswith(b"\n"):
        _wait_readable(stream, deadline)
        chunk = stream.read(1)
        if not chunk:
            raise McpClientError("MCP process ended before sending a complete line.")
        data += chunk
        if len(data) > 5_000_000:
            raise McpClientError("MCP response line is too large.")
    return data.rstrip(b"\r\n")


def _wait_readable(stream: Any, deadline: float) -> None:
    # If the BufferedReader already has bytes buffered in user space the OS
    # pipe will look idle to select(), so check the in-process buffer first.
    peek = getattr(stream, "peek", None)
    if peek is not None:
        try:
            if peek(1):
                return
        except ValueError:
            # stream is closed; let the subsequent read() surface the EOF.
            return
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise McpClientError("MCP response timed out.")
    readable, _, _ = select.select([stream], [], [], remaining)
    if not readable:
        raise McpClientError("MCP response timed out.")
