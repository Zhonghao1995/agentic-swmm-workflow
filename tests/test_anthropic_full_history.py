"""Anthropic provider must replay full history each turn (review P1-5).

The Messages API is stateless and requires each ``tool_result`` to follow its
matching ``tool_use`` in the same messages array. The planner passes only the
delta each turn (goal, then just tool outputs), so the provider must accumulate
the conversation itself. Before the fix, turn 2 sent a lone ``tool_result`` with
no preceding ``tool_use`` and no original goal.
"""

from __future__ import annotations

from agentic_swmm.providers.anthropic_api import AnthropicProvider


_TOOLS = [{"type": "function", "name": "read_file", "parameters": {"type": "object", "properties": {}}}]


def test_turn_two_replays_goal_tool_use_and_tool_result(monkeypatch) -> None:
    monkeypatch.delenv("AISWMM_ANTHROPIC_MOCK_TOOL_CALLS", raising=False)
    monkeypatch.delenv("AISWMM_ANTHROPIC_MOCK_RESPONSE", raising=False)
    provider = AnthropicProvider(model="claude-x", api_key="test-key")

    sent: list[dict] = []
    responses = [
        {"id": "resp1", "content": [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "x"}}]},
        {"id": "resp2", "content": [{"type": "text", "text": "done"}]},
    ]

    def fake_post(payload):
        sent.append(payload)
        return responses[len(sent) - 1]

    monkeypatch.setattr(provider, "_post", fake_post)

    # Turn 1 (session start): the planner sends the goal.
    provider.respond_with_tools(
        system_prompt="sys",
        input_items=[{"role": "user", "content": "Goal: do the thing"}],
        tools=_TOOLS,
        previous_response_id=None,
    )
    # Turn 2 (continuation): the planner sends only the tool output.
    provider.respond_with_tools(
        system_prompt="sys",
        input_items=[{"type": "function_call_output", "call_id": "t1", "output": "{}"}],
        tools=_TOOLS,
        previous_response_id="resp1",
    )

    turn2 = sent[1]["messages"]
    assert [m["role"] for m in turn2] == ["user", "assistant", "user"]
    assert "Goal: do the thing" in str(turn2[0]["content"])
    assert turn2[1]["content"][0]["type"] == "tool_use"
    assert turn2[1]["content"][0]["id"] == "t1"
    tool_result = turn2[2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "t1"


def test_new_session_resets_history(monkeypatch) -> None:
    monkeypatch.delenv("AISWMM_ANTHROPIC_MOCK_TOOL_CALLS", raising=False)
    provider = AnthropicProvider(model="claude-x", api_key="test-key")
    sent: list[dict] = []
    monkeypatch.setattr(provider, "_post", lambda p: (sent.append(p) or {"id": "r", "content": [{"type": "text", "text": "ok"}]}))

    provider.respond_with_tools(system_prompt="s", input_items=[{"role": "user", "content": "first"}], tools=_TOOLS, previous_response_id=None)
    provider.respond_with_tools(system_prompt="s", input_items=[{"role": "user", "content": "second"}], tools=_TOOLS, previous_response_id=None)

    # Second session start must not carry the first session's messages.
    assert "first" not in str(sent[1]["messages"])
    assert "second" in str(sent[1]["messages"])


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
