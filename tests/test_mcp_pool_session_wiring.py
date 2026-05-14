"""Wiring tests: the per-process MCP session pool.

PRD-X User Story 5 + Done Criteria: instantiating ``MCPPool`` from the
runtime ``mcp.json`` registry, binding the singleton, and using
``session_pool()`` from elsewhere in the code base. These tests do not
spawn Node — they stub ``subprocess.Popen`` so the pool's lifecycle is
exercised purely in process.
"""

from __future__ import annotations

import pytest

from agentic_swmm.agent import mcp_pool


@pytest.fixture(autouse=True)
def _reset_session_pool() -> None:
    """Ensure each test starts with no bound pool."""

    mcp_pool.clear_session_pool()
    yield
    mcp_pool.clear_session_pool()


def test_session_pool_returns_none_before_binding() -> None:
    assert mcp_pool.session_pool() is None


def test_ensure_session_pool_builds_from_runtime_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper used by runtime_loop / single_shot must build a pool
    from the user's runtime ``mcp.json`` records and bind it to the
    process. Calling it twice in one session reuses the existing pool —
    the second call must not spin up a parallel pool.
    """

    fake_records = [
        {"name": "swmm-builder", "enabled": True, "command": "node", "args": ["mcp/swmm-builder/server.js"]},
        {"name": "swmm-runner", "enabled": True, "command": "node", "args": ["mcp/swmm-runner/server.js"]},
    ]
    monkeypatch.setattr(mcp_pool, "_load_mcp_registry", lambda: fake_records)
    # No real spawns — we only assert the pool object is constructed and bound.

    pool = mcp_pool.ensure_session_pool()
    assert pool is mcp_pool.session_pool()
    assert pool.list_servers() == ["swmm-builder", "swmm-runner"]

    again = mcp_pool.ensure_session_pool()
    assert again is pool, "ensure_session_pool must reuse the bound singleton"


def test_ensure_session_pool_returns_none_when_registry_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If there are no MCP servers configured (degraded install), the
    helper returns ``None`` rather than binding an empty pool — that way
    ``session_pool()`` callers fall back to spawn-per-call seamlessly.
    """

    monkeypatch.setattr(mcp_pool, "_load_mcp_registry", lambda: [])
    assert mcp_pool.ensure_session_pool() is None
    assert mcp_pool.session_pool() is None
