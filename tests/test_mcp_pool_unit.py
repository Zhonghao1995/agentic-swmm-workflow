"""Unit tests for ``agentic_swmm.agent.mcp_pool.MCPPool``.

These tests run without Node by stubbing ``subprocess.Popen`` so the pool
believes it is talking to a long-running MCP server. The stub records the
JSON-RPC messages the pool writes and replies with crafted NDJSON frames.

Together they pin the deep-module contract documented in PRD-X:
``MCPPool(server_registry) -> ConnectedPool`` with lazy-by-server startup,
single-initialize-per-server, error isolation, and idempotent shutdown.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import pytest

from agentic_swmm.agent import mcp_pool
from agentic_swmm.agent.mcp_client import McpClientError


# ---------------------------------------------------------------------------
# Stub subprocess that speaks the NDJSON MCP framing without needing Node.
# ---------------------------------------------------------------------------


@dataclass
class _Pipe:
    buffer: bytes = b""
    closed: bool = False

    def write(self, data: bytes) -> int:
        self.buffer += data
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _StubStdout:
    """A queue of NDJSON lines the pool can ``readline`` / ``peek`` from."""

    def __init__(self) -> None:
        self._queue: list[bytes] = []
        self._buffer: bytes = b""

    def push(self, payload: dict[str, Any]) -> None:
        self._queue.append(
            json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        )

    def _ensure_buffer(self) -> None:
        if not self._buffer and self._queue:
            self._buffer = self._queue.pop(0)

    def read(self, n: int = -1) -> bytes:
        self._ensure_buffer()
        if not self._buffer:
            return b""
        if n < 0 or n >= len(self._buffer):
            data = self._buffer
            self._buffer = b""
            return data
        data = self._buffer[:n]
        self._buffer = self._buffer[n:]
        return data

    def peek(self, n: int = 1) -> bytes:
        self._ensure_buffer()
        return self._buffer[:n] if self._buffer else b""

    def close(self) -> None:
        pass


class _StubPopen:
    """Looks enough like ``subprocess.Popen`` for ``MCPPool``."""

    instances: list["_StubPopen"] = []

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.stdin = _Pipe()
        self.stdout = _StubStdout()
        self.stderr = io.BytesIO()
        self.returncode: int | None = None
        self.terminate_called = 0
        self.kill_called = 0
        self.pid = 10_000 + len(_StubPopen.instances)
        _StubPopen.instances.append(self)

    # --- subprocess.Popen surface --------------------------------------------------

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_called += 1
        self.returncode = 0

    def kill(self) -> None:
        self.kill_called += 1
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    # --- helpers for tests ---------------------------------------------------------

    def written_messages(self) -> list[dict[str, Any]]:
        return [
            json.loads(chunk.decode("utf-8"))
            for chunk in self.stdin.buffer.split(b"\n")
            if chunk
        ]


def _install_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    on_spawn: Callable[[_StubPopen], None] | None = None,
    fail_for: Iterable[str] = (),
) -> list[_StubPopen]:
    """Replace ``mcp_pool.subprocess.Popen`` with a stub.

    ``on_spawn`` is called every time the pool spawns a server so tests can
    pre-load NDJSON replies. ``fail_for`` is a set of server names whose
    spawn raises ``FileNotFoundError`` to model 'node not installed'.
    """

    _StubPopen.instances = []
    fail_set = set(fail_for)

    def factory(command, **kwargs):  # noqa: ANN001 — match subprocess.Popen
        for name in fail_set:
            if name in " ".join(command):
                raise FileNotFoundError(command[0])
        proc = _StubPopen(list(command))
        if on_spawn is not None:
            on_spawn(proc)
        return proc

    monkeypatch.setattr(mcp_pool.subprocess, "Popen", factory)
    # Skip the on-disk preflight; in stub-mode we trust the spec.
    monkeypatch.setattr(mcp_pool, "_preflight", lambda spec: None)
    return _StubPopen.instances


def _seed_initialize_reply(proc: _StubPopen) -> None:
    proc.stdout.push(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": proc.command[-1], "version": "0.0"},
            },
        }
    )


def _two_server_registry() -> list[mcp_pool.ServerSpec]:
    return [
        mcp_pool.ServerSpec(name="alpha", command="node", args=["alpha.js"]),
        mcp_pool.ServerSpec(name="beta", command="node", args=["beta.js"]),
    ]


# ---------------------------------------------------------------------------
# Tests — module contract
# ---------------------------------------------------------------------------


def test_list_servers_returns_registered_names_without_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = _install_stub(monkeypatch)
    pool = mcp_pool.MCPPool(_two_server_registry())

    assert pool.list_servers() == ["alpha", "beta"]
    assert instances == [], "MCPPool.__init__ must be lazy — never spawn at construction"


def test_list_tools_lazy_spawns_only_target_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def on_spawn(proc: _StubPopen) -> None:
        _seed_initialize_reply(proc)
        proc.stdout.push(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": [{"name": "do_thing"}]},
            }
        )

    instances = _install_stub(monkeypatch, on_spawn=on_spawn)
    pool = mcp_pool.MCPPool(_two_server_registry())

    tools = pool.list_tools("alpha")

    assert tools == [{"name": "do_thing"}]
    # Exactly one process spawned, for "alpha" only.
    assert len(instances) == 1
    assert "alpha.js" in " ".join(instances[0].command)


def test_repeated_list_tools_reuses_same_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def on_spawn(proc: _StubPopen) -> None:
        _seed_initialize_reply(proc)
        for i in range(2, 5):
            proc.stdout.push(
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "result": {"tools": [{"name": "do_thing"}]},
                }
            )

    instances = _install_stub(monkeypatch, on_spawn=on_spawn)
    pool = mcp_pool.MCPPool(_two_server_registry())

    pool.list_tools("alpha")
    pool.list_tools("alpha")
    pool.list_tools("alpha")

    assert len(instances) == 1, "pool must persist stdio across calls"
    written = instances[0].written_messages()
    methods = [m.get("method") for m in written]
    # One handshake (initialize + notifications/initialized) followed by three tools/list.
    assert methods.count("initialize") == 1
    assert methods.count("tools/list") == 3


def test_call_tool_sends_tools_call_with_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def on_spawn(proc: _StubPopen) -> None:
        _seed_initialize_reply(proc)
        proc.stdout.push(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": "ok"}]},
            }
        )

    instances = _install_stub(monkeypatch, on_spawn=on_spawn)
    pool = mcp_pool.MCPPool(_two_server_registry())

    result = pool.call_tool("alpha", "do_thing", {"x": 1})

    assert result == {"content": [{"type": "text", "text": "ok"}]}
    written = instances[0].written_messages()
    call_msg = next(m for m in written if m.get("method") == "tools/call")
    assert call_msg["params"] == {"name": "do_thing", "arguments": {"x": 1}}


def test_unknown_server_raises_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stub(monkeypatch)
    pool = mcp_pool.MCPPool(_two_server_registry())

    with pytest.raises(McpClientError, match="unknown MCP server"):
        pool.list_tools("nope")


def test_startup_failure_is_isolated_to_failing_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bad server must not poison the pool."""

    def on_spawn(proc: _StubPopen) -> None:
        _seed_initialize_reply(proc)
        proc.stdout.push(
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "ok"}]}}
        )

    _install_stub(monkeypatch, on_spawn=on_spawn, fail_for=["alpha.js"])
    pool = mcp_pool.MCPPool(_two_server_registry())

    # "alpha" fails — call returns a typed error, does not raise unhandled.
    with pytest.raises(McpClientError):
        pool.list_tools("alpha")

    # "beta" still works — the failure of alpha did not break the pool.
    tools = pool.list_tools("beta")
    assert tools == [{"name": "ok"}]


def test_failed_server_is_remembered_and_not_respawned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stub(monkeypatch, fail_for=["alpha.js"])
    pool = mcp_pool.MCPPool([mcp_pool.ServerSpec("alpha", "node", ["alpha.js"])])

    with pytest.raises(McpClientError):
        pool.list_tools("alpha")
    # Subsequent calls should also error without trying to spawn again —
    # otherwise the user sees N copies of the same FileNotFoundError.
    with pytest.raises(McpClientError):
        pool.list_tools("alpha")


def test_shutdown_terminates_only_started_servers_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def on_spawn(proc: _StubPopen) -> None:
        _seed_initialize_reply(proc)
        proc.stdout.push(
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
        )

    instances = _install_stub(monkeypatch, on_spawn=on_spawn)
    pool = mcp_pool.MCPPool(_two_server_registry())

    pool.list_tools("alpha")  # spawn alpha only
    assert len(instances) == 1

    pool.shutdown()
    assert instances[0].terminate_called == 1
    # Idempotent: a second shutdown must not raise and must not terminate twice.
    pool.shutdown()
    assert instances[0].terminate_called == 1


def test_initialize_error_payload_surfaces_as_mcp_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def on_spawn(proc: _StubPopen) -> None:
        proc.stdout.push(
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "boom"}}
        )

    _install_stub(monkeypatch, on_spawn=on_spawn)
    pool = mcp_pool.MCPPool([mcp_pool.ServerSpec("alpha", "node", ["alpha.js"])])

    with pytest.raises(McpClientError, match="boom"):
        pool.list_tools("alpha")
