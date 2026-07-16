"""Tool execution must report failure honestly (review P1-6, P2-3).

Two seams were letting a failure read as success or crash the whole session:

* ``_wrap_mcp_result`` hardcoded ``ok=True`` even when the MCP tool set
  ``isError: true`` (P1-6).
* ``AgentToolRegistry.execute`` had no exception boundary, so a handler that
  raised (e.g. a missing required argument the model omitted) propagated as an
  uncaught exception past the fail-soft machinery (P2-3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_swmm.agent.tool_handlers._shared import _wrap_mcp_result
from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolSpec
from agentic_swmm.agent.types import ToolCall


def test_mcp_iserror_result_marks_not_ok() -> None:
    call = ToolCall("call_mcp_tool", {})
    result = {"isError": True, "content": [{"type": "text", "text": "boom"}]}
    wrapped = _wrap_mcp_result(call, "swmm-runner", "run", result)
    assert wrapped["ok"] is False
    assert "boom" in wrapped["excerpt"]
    assert "error" in wrapped["summary"].lower()


def test_mcp_clean_result_stays_ok() -> None:
    call = ToolCall("call_mcp_tool", {})
    result = {"content": [{"type": "text", "text": "done"}]}
    wrapped = _wrap_mcp_result(call, "swmm-runner", "run", result)
    assert wrapped["ok"] is True


def test_generic_handler_exception_becomes_failed_result() -> None:
    registry = AgentToolRegistry()

    def _boom(call: ToolCall, session_dir: Path) -> dict:
        raise KeyError("skill_name")

    registry._tools["_boom"] = ToolSpec(
        name="_boom", description="", parameters={}, handler=_boom
    )
    result = registry.execute(ToolCall("_boom", {}), session_dir=Path("."))
    assert result["ok"] is False
    assert "KeyError" in result["summary"]


def test_control_flow_exception_still_propagates() -> None:
    from agentic_swmm.agent.memory_informed_policy import MemoryHITLRequired

    registry = AgentToolRegistry()

    def _escalate(call: ToolCall, session_dir: Path) -> dict:
        raise MemoryHITLRequired("needs a human")

    registry._tools["_escalate"] = ToolSpec(
        name="_escalate", description="", parameters={}, handler=_escalate
    )
    with pytest.raises(MemoryHITLRequired):
        registry.execute(ToolCall("_escalate", {}), session_dir=Path("."))


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
