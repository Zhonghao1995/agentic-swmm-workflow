"""Unit tests for ``_make_mcp_routed_handler`` (PRD-Y).

The factory turns a ``(server, tool)`` pair into a ToolSpec handler that
routes the call through the session-bound ``MCPPool``. The handler
contract documented in PRD-Y "Handler rewrite — uniform pattern":

* On success the handler wraps the pool's ``result`` dict into a
  ToolSpec response (``{tool, args, ok: True, results, summary}``).
* On ``McpClientError`` the handler returns ``ok=False`` with a
  ``"MCP transport failed: ..."`` summary — never raises. This keeps
  PR #37's planner fail-soft loop intact: the LLM still sees the
  failure as a tool result it can react to.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentic_swmm.agent import mcp_pool, tool_registry
from agentic_swmm.agent.mcp_client import McpClientError
from agentic_swmm.agent.types import ToolCall


class _StubPool:
    """Minimal pool stub: records ``call_tool`` invocations + canned reply."""

    def __init__(self, *, response: dict[str, Any] | None = None, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._response = response if response is not None else {"result": "ok"}
        self._raises = raises

    def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout: int = 60,
    ) -> dict[str, Any]:
        self.calls.append((server, tool, arguments))
        if self._raises is not None:
            raise self._raises
        return self._response


@pytest.fixture(autouse=True)
def _bind_stub_pool() -> None:
    """Clear any previously-bound pool around each test."""
    mcp_pool.clear_session_pool()
    yield
    mcp_pool.clear_session_pool()


def _bind(pool: _StubPool) -> None:
    mcp_pool.bind_session_pool(pool)  # type: ignore[arg-type]


def test_mcp_routed_handler_calls_pool_with_server_tool_and_args(
    tmp_path: Path,
) -> None:
    pool = _StubPool(response={"content": [{"type": "text", "text": "ran"}]})
    _bind(pool)

    handler = tool_registry._make_mcp_routed_handler("swmm-builder", "build_inp")
    call = ToolCall("build_inp", {"x": 1, "y": "two"})

    result = handler(call, tmp_path)

    assert pool.calls == [("swmm-builder", "build_inp", {"x": 1, "y": "two"})]
    assert result["tool"] == "build_inp"
    assert result["ok"] is True
    # MCP result body is surfaced under ``results`` so the LLM sees it.
    assert result["results"] == {"content": [{"type": "text", "text": "ran"}]}
    assert "swmm-builder.build_inp" in result["summary"]


def test_mcp_routed_handler_fails_soft_on_mcp_client_error(tmp_path: Path) -> None:
    pool = _StubPool(raises=McpClientError("node not on PATH"))
    _bind(pool)

    handler = tool_registry._make_mcp_routed_handler("swmm-runner", "swmm_run")
    call = ToolCall("run_swmm_inp", {"inp_path": "x.inp"})

    result = handler(call, tmp_path)

    assert result["ok"] is False
    assert result["tool"] == "run_swmm_inp"
    assert "MCP transport failed" in result["summary"]
    assert "node not on PATH" in result["summary"]


def test_mcp_routed_handler_when_pool_is_unbound_fails_soft(tmp_path: Path) -> None:
    """If ``ensure_session_pool`` returns ``None`` (degraded install / no
    MCP registry), the handler must report a clean transport failure
    instead of crashing.
    """

    # Force ensure_session_pool to return None — registry empty.
    import agentic_swmm.agent.tool_registry as tr

    def fake_pool() -> None:
        return None

    original = tr.ensure_session_pool
    tr.ensure_session_pool = fake_pool  # type: ignore[assignment]
    try:
        handler = tr._make_mcp_routed_handler("swmm-builder", "build_inp")
        result = handler(ToolCall("build_inp", {}), tmp_path)
    finally:
        tr.ensure_session_pool = original  # type: ignore[assignment]

    assert result["ok"] is False
    assert "MCP transport" in result["summary"]
