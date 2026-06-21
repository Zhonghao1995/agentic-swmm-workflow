"""Unit tests for MCP transport resilience in ``mcp_pool`` (respawn + retry).

These stub ``subprocess.Popen`` with a child whose stdout yields a few NDJSON
frames and then simulates a crash: ``peek()`` reports readable but ``read()``
returns EOF, so ``mcp_client._readline`` raises
"MCP process ended before sending a complete line." — exactly the dominant
real-world failure. They pin three contracts:

* a child that dies during *startup* is respawned and the request replayed,
  for any method (the call was never sent);
* a child that dies *after the request is on the wire* is replayed only for
  idempotent methods (``tools/list``), never for ``tools/call``;
* a recovered transient drop does not permanently poison the server.
"""

from __future__ import annotations

import io
import json

import pytest

from agentic_swmm.agent import mcp_pool
from agentic_swmm.agent.mcp_client import McpClientError


class _Pipe:
    def __init__(self) -> None:
        self.buffer = b""

    def write(self, data: bytes) -> int:
        self.buffer += data
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FramesThenEOF:
    """stdout stub: emits queued frames, then simulates a dead child.

    Once the queue drains, ``peek`` reports readable (non-empty) while
    ``read`` returns EOF, which drives ``_readline`` into its
    "process ended" branch instead of the timeout/select path.
    """

    def __init__(self) -> None:
        self._frames: list[bytes] = []
        self._buf = b""

    def push(self, payload: dict) -> None:
        self._frames.append(
            json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        )

    def _fill(self) -> None:
        if not self._buf and self._frames:
            self._buf = self._frames.pop(0)

    def read(self, n: int = -1) -> bytes:
        self._fill()
        if not self._buf:
            return b""  # EOF -> child died
        if n < 0 or n >= len(self._buf):
            data = self._buf
            self._buf = b""
            return data
        data = self._buf[:n]
        self._buf = self._buf[n:]
        return data

    def peek(self, n: int = 1) -> bytes:
        self._fill()
        return self._buf[:n] if self._buf else b"\x00"

    def close(self) -> None:
        pass


class _StubPopen:
    instances: list["_StubPopen"] = []

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.stdin = _Pipe()
        self.stdout = _FramesThenEOF()
        self.stderr = io.BytesIO()
        self.returncode: int | None = None
        self.pid = 20_000 + len(_StubPopen.instances)
        _StubPopen.instances.append(self)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _init_reply(proc: _StubPopen) -> None:
    proc.stdout.push(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "alpha", "version": "0"},
            },
        }
    )


def _install(monkeypatch, on_spawn) -> list[_StubPopen]:
    _StubPopen.instances = []

    def factory(command, **kwargs):  # noqa: ANN001 — match subprocess.Popen
        proc = _StubPopen(list(command))
        on_spawn(proc)
        return proc

    monkeypatch.setattr(mcp_pool.subprocess, "Popen", factory)
    monkeypatch.setattr(mcp_pool, "_preflight", lambda spec: None)
    # No real backoff in tests.
    monkeypatch.setattr(mcp_pool.time, "sleep", lambda *_a, **_k: None)
    return _StubPopen.instances


def _spec() -> list:
    return [mcp_pool.ServerSpec(name="alpha", command="node", args=["alpha.js"])]


def test_call_tool_retries_after_startup_death(monkeypatch) -> None:
    """First child dies during init; the call is replayed against a respawn."""

    def on_spawn(proc: _StubPopen) -> None:
        if len(_StubPopen.instances) == 1:
            return  # no frames -> init _read hits EOF -> transient death
        _init_reply(proc)
        proc.stdout.push(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": "ok"}]},
            }
        )

    instances = _install(monkeypatch, on_spawn)
    pool = mcp_pool.MCPPool(_spec())

    result = pool.call_tool("alpha", "do_thing", {"x": 1})

    assert result == {"content": [{"type": "text", "text": "ok"}]}
    assert len(instances) == 2, "a startup death must trigger one respawn+retry"
    assert pool._handles["alpha"].error is None, "transient death must not poison"


def test_list_tools_retries_after_send_phase_death(monkeypatch) -> None:
    """Child inits, then dies before answering tools/list -> idempotent replay."""

    def on_spawn(proc: _StubPopen) -> None:
        _init_reply(proc)
        if len(_StubPopen.instances) >= 2:
            proc.stdout.push(
                {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "t"}]}}
            )

    instances = _install(monkeypatch, on_spawn)
    pool = mcp_pool.MCPPool(_spec())

    tools = pool.list_tools("alpha")

    assert tools == [{"name": "t"}]
    assert len(instances) == 2


def test_call_tool_not_retried_after_send_phase_death(monkeypatch) -> None:
    """A drop after tools/call is on the wire must NOT be replayed (idempotency)."""

    def on_spawn(proc: _StubPopen) -> None:
        _init_reply(proc)  # init ok, but never answer the call -> EOF on that read

    instances = _install(monkeypatch, on_spawn)
    pool = mcp_pool.MCPPool(_spec())

    with pytest.raises(McpClientError, match="process ended"):
        pool.call_tool("alpha", "do_thing", {})

    assert len(instances) == 1, "tools/call must not respawn-and-replay"
    # Dead child cleared so the next independent call starts fresh.
    assert pool._handles["alpha"].proc is None


def test_persistent_startup_death_surfaces_after_retries(monkeypatch) -> None:
    """If every spawn dies during init, the error surfaces (bounded retries)."""

    def on_spawn(proc: _StubPopen) -> None:
        return  # every child dies during init

    instances = _install(monkeypatch, on_spawn)
    pool = mcp_pool.MCPPool(_spec())

    with pytest.raises(McpClientError, match="process ended"):
        pool.list_tools("alpha")

    # Initial attempt + _MAX_TRANSPORT_RETRIES respawns.
    assert len(instances) == mcp_pool._MAX_TRANSPORT_RETRIES + 1
