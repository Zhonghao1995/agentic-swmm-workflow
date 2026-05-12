from __future__ import annotations

import json
import select
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
    response = call_mcp(command, args, "tools/list", {}, timeout=timeout)
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    tools = result.get("tools", [])
    return tools if isinstance(tools, list) else []


def call_tool(command: str, args: list[str], tool_name: str, arguments: dict[str, Any], *, timeout: int = 60) -> dict[str, Any]:
    response = call_mcp(command, args, "tools/call", {"name": tool_name, "arguments": arguments}, timeout=timeout)
    result = response.get("result")
    return result if isinstance(result, dict) else {"result": result}


def _send(proc: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise McpClientError("MCP process stdin is unavailable.")
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    proc.stdin.flush()


def _read(proc: subprocess.Popen[bytes], *, timeout: int) -> dict[str, Any]:
    if proc.stdout is None:
        raise McpClientError("MCP process stdout is unavailable.")
    header = _read_until(proc.stdout, b"\r\n\r\n", timeout=timeout)
    headers = header.decode("ascii", errors="replace").split("\r\n")
    length = None
    for line in headers:
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
            break
    if length is None:
        raise McpClientError("MCP response missing Content-Length header.")
    body = _read_exact(proc.stdout, length, timeout=timeout)
    parsed = json.loads(body.decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _read_until(stream: Any, marker: bytes, *, timeout: int) -> bytes:
    deadline = time.monotonic() + timeout
    data = b""
    while marker not in data:
        _wait_readable(stream, deadline)
        chunk = stream.read(1)
        if not chunk:
            raise McpClientError("MCP process ended before sending a complete response.")
        data += chunk
        if len(data) > 100_000:
            raise McpClientError("MCP response header is too large.")
    return data[: data.index(marker)]


def _read_exact(stream: Any, length: int, *, timeout: int) -> bytes:
    deadline = time.monotonic() + timeout
    data = b""
    while len(data) < length:
        _wait_readable(stream, deadline)
        chunk = stream.read(length - len(data))
        if not chunk:
            raise McpClientError("MCP response ended before the declared body length.")
        data += chunk
    return data


def _wait_readable(stream: Any, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise McpClientError("MCP response timed out.")
    readable, _, _ = select.select([stream], [], [], remaining)
    if not readable:
        raise McpClientError("MCP response timed out.")
