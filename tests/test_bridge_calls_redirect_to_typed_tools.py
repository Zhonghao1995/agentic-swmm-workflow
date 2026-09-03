"""A bridge call to a tool with a typed ToolSpec is redirected before any prompt (F-56b).

Live finding 2026-09-02 (scenario S17, turn 4): asked to check the network,
the planner ran `call_mcp_tool swmm-network.qa` (ten tool calls, 173k
tokens, an untyped result) although the typed `network_qa` was in its
schema set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_swmm.agent import executor as executor_mod
from agentic_swmm.agent import permissions
from agentic_swmm.agent.executor import AgentExecutor as Executor
from agentic_swmm.agent.mcp_coverage import typed_tool_for
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


def test_the_binding_table_names_the_typed_tool():
    assert typed_tool_for("swmm-network", "qa") == "network_qa"
    assert typed_tool_for("swmm-network", "no-such-tool") is None
    assert typed_tool_for("no-such-server", "qa") is None


@pytest.fixture
def executor(tmp_path, monkeypatch):
    def _no_prompt(*args, **kwargs):
        raise AssertionError("a redirected bridge call must not prompt")

    monkeypatch.setattr(permissions, "request_approval", _no_prompt)
    registry = AgentToolRegistry()
    return Executor(registry=registry, session_dir=tmp_path, trace_path=tmp_path / "t.jsonl", profile=Profile.QUICK)


def test_a_bound_bridge_call_is_answered_with_the_typed_name(executor, tmp_path):
    call = ToolCall(name="call_mcp_tool", args={"server": "swmm-network", "tool": "qa", "arguments": {"networkJson": "x.json"}})
    result = executor.execute(call)
    assert result["ok"] is False
    assert result["redirect_to"] == "network_qa"
    assert "network_qa" in result["hint"]
    assert "typed result shape" in result["hint"]
    events = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert any(e.get("event") == "tool_result" and e.get("redirect_to") == "network_qa" for e in events)


def test_arguments_the_typed_tool_cannot_take_keep_the_bridge(executor):
    # The live S17/S18 call: the MCP qa validates an INP (inpPath); the typed
    # network_qa validates a network JSON (network_json). No redirect.
    call = ToolCall(name="call_mcp_tool", args={"server": "swmm-network", "tool": "qa", "arguments": {"inpPath": "runs/x/05_builder/model.inp"}})
    assert executor._typed_redirect(call) is None


def test_matching_arguments_are_redirected(executor):
    call = ToolCall(name="call_mcp_tool", args={"server": "swmm-network", "tool": "qa", "arguments": {"networkJson": "runs/x/network.json", "reportJson": "runs/x/qa.json"}})
    redirect = executor._typed_redirect(call)
    assert redirect is not None and redirect["redirect_to"] == "network_qa"


def test_an_unbound_bridge_call_is_not_touched(executor):
    call = ToolCall(name="call_mcp_tool", args={"server": "swmm-network", "tool": "no-such-tool", "arguments": {}})
    assert executor._typed_redirect(call) is None
