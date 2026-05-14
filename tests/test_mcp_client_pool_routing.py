"""When a session pool is bound, ``mcp_client.list_tools`` and
``mcp_client.call_tool`` must route through the pool instead of spawning
a fresh node child every call. This is the visible behaviour change the
runtime expects after PRD-X.
"""

from __future__ import annotations

import pytest

from agentic_swmm.agent import mcp_client, mcp_pool


class _RecordingPool:
    """Stand-in for ``MCPPool`` that records routed calls and returns canned data."""

    def __init__(self, server_specs: list[mcp_pool.ServerSpec]) -> None:
        self._handles = {spec.name: object() for spec in server_specs}
        self.specs = list(server_specs)
        self.list_calls: list[str] = []
        self.tool_calls: list[tuple[str, str, dict]] = []

    def list_tools(self, server: str, *, timeout: int = 20) -> list[dict]:
        self.list_calls.append(server)
        return [{"name": "fake_tool"}]

    def call_tool(self, server: str, tool: str, arguments: dict, *, timeout: int = 60) -> dict:
        self.tool_calls.append((server, tool, dict(arguments)))
        return {"content": [{"type": "text", "text": "ok"}]}


@pytest.fixture(autouse=True)
def _reset_session_pool() -> None:
    mcp_pool.clear_session_pool()
    yield
    mcp_pool.clear_session_pool()


def test_list_tools_routes_through_bound_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = mcp_pool.ServerSpec(name="swmm-builder", command="node", args=["mcp/swmm-builder/server.js"])
    pool = _RecordingPool([spec])
    mcp_pool.bind_session_pool(pool)  # type: ignore[arg-type]

    # If routing fails open, the test would hang in subprocess.Popen — so
    # also stub Popen as a hard guard. Any unexpected fall-through is caught.
    def _no_subprocess(*_a, **_kw):  # pragma: no cover — safety net
        raise AssertionError("mcp_client should have routed through the pool")

    monkeypatch.setattr(mcp_client.subprocess, "Popen", _no_subprocess)

    tools = mcp_client.list_tools("node", ["mcp/swmm-builder/server.js"])

    assert tools == [{"name": "fake_tool"}]
    assert pool.list_calls == ["swmm-builder"]


def test_call_tool_routes_through_bound_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = mcp_pool.ServerSpec(name="swmm-runner", command="node", args=["mcp/swmm-runner/server.js"])
    pool = _RecordingPool([spec])
    mcp_pool.bind_session_pool(pool)  # type: ignore[arg-type]

    def _no_subprocess(*_a, **_kw):  # pragma: no cover — safety net
        raise AssertionError("mcp_client should have routed through the pool")

    monkeypatch.setattr(mcp_client.subprocess, "Popen", _no_subprocess)

    result = mcp_client.call_tool(
        "node",
        ["mcp/swmm-runner/server.js"],
        "swmm_run",
        {"inp": "x.inp", "runDir": "/tmp/runs/a"},
    )

    assert result == {"content": [{"type": "text", "text": "ok"}]}
    assert pool.tool_calls == [
        ("swmm-runner", "swmm_run", {"inp": "x.inp", "runDir": "/tmp/runs/a"})
    ]


def test_routing_falls_back_to_spawn_when_command_args_unrecognised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the bound pool does not contain a matching spec, fall through to
    the historical spawn-per-call path so tests that mock Popen for ad-hoc
    server invocations still work.

    We verify by asserting that the pool's ``list_tools`` is NOT invoked
    (the test stops at the preflight check, which is still part of the
    spawn-per-call code path — not pool routing).
    """

    spec = mcp_pool.ServerSpec(name="swmm-builder", command="node", args=["mcp/swmm-builder/server.js"])
    pool = _RecordingPool([spec])
    mcp_pool.bind_session_pool(pool)  # type: ignore[arg-type]

    # Unmatched (command, args): pool routing must not fire. Preflight will
    # raise McpClientError because mcp/other-server/server.js doesn't have
    # node_modules. That's the fallback path — we just want to confirm the
    # pool wasn't asked to handle the call.
    with pytest.raises(mcp_client.McpClientError):
        mcp_client.list_tools("node", ["mcp/other-server/server.js"])

    assert pool.list_calls == [], "unmatched (command, args) must skip the pool"
