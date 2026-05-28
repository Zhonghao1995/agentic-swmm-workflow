"""AnthropicProvider unit tests (raw Messages API, no SDK).

The provider is a pure-stdlib ``urllib`` client. These tests cover the
two translation directions plus response parsing without any network:

* tool-schema translation (OpenAI-shaped ``parameters`` ->
  Anthropic ``input_schema``);
* ``input_items`` -> Anthropic ``messages`` (text, ``tool_use`` for a
  prior function_call, ``tool_result`` for a function_call_output);
* response parsing (``text`` blocks -> text, ``tool_use`` blocks ->
  ``ProviderToolCall``);
* the ``AISWMM_ANTHROPIC_MOCK_*`` env hooks;
* the missing-key error (no spend).

The single HTTP path is exercised by monkeypatching the provider's
``_post`` so we can assert the exact request body without a socket.
"""
from __future__ import annotations

import json

import pytest

from agentic_swmm.providers.anthropic_api import (
    ANTHROPIC_MAX_TOKENS,
    ANTHROPIC_MESSAGES_URL,
    ANTHROPIC_VERSION,
    AnthropicProvider,
    _extract_output_text,
    _extract_tool_calls,
    _translate_input_items,
    _translate_tools,
)
from agentic_swmm.providers.base import (
    ProviderResult,
    ProviderToolCall,
    ProviderToolResponse,
)


def _provider(**kw):
    kw.setdefault("model", "claude-sonnet-4-6")
    kw.setdefault("api_key", "sk-ant-test")
    return AnthropicProvider(**kw)


class TestToolSchemaTranslation:
    def test_parameters_renamed_to_input_schema(self):
        tools = [
            {
                "type": "function",
                "name": "doctor",
                "description": "diagnose the install",
                "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
            }
        ]
        out = _translate_tools(tools)
        assert out == [
            {
                "name": "doctor",
                "description": "diagnose the install",
                "input_schema": {
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                },
            }
        ]

    def test_missing_description_defaults_to_empty_string(self):
        out = _translate_tools([{"type": "function", "name": "f", "parameters": {"type": "object"}}])
        assert out[0]["description"] == ""

    def test_missing_parameters_yields_empty_object_schema(self):
        out = _translate_tools([{"type": "function", "name": "f"}])
        assert out[0]["input_schema"] == {"type": "object", "properties": {}}

    def test_descriptor_without_name_is_skipped(self):
        out = _translate_tools([{"type": "function", "parameters": {}}, {"name": "ok", "parameters": {}}])
        assert [t["name"] for t in out] == ["ok"]


class TestInputItemsTranslation:
    def test_user_text_item_becomes_user_message(self):
        msgs = _translate_input_items([{"role": "user", "content": "hello"}])
        assert msgs == [{"role": "user", "content": "hello"}]

    def test_function_call_becomes_assistant_tool_use_block(self):
        msgs = _translate_input_items(
            [{"type": "function_call", "call_id": "c1", "name": "run", "arguments": '{"a": 1}'}]
        )
        assert msgs == [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "c1", "name": "run", "input": {"a": 1}}
                ],
            }
        ]

    def test_function_call_output_becomes_user_tool_result_keyed_by_id(self):
        msgs = _translate_input_items(
            [{"type": "function_call_output", "call_id": "c1", "output": '{"ok": true}'}]
        )
        assert msgs == [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "c1", "content": '{"ok": true}'}
                ],
            }
        ]

    def test_full_round_trip_history_order_preserved(self):
        items = [
            {"role": "user", "content": "do it"},
            {"type": "function_call", "call_id": "c1", "name": "run", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "done"},
        ]
        msgs = _translate_input_items(items)
        assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
        assert msgs[1]["content"][0]["type"] == "tool_use"
        assert msgs[2]["content"][0]["type"] == "tool_result"

    def test_rich_content_blocks_are_flattened_to_text(self):
        items = [{"role": "assistant", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]
        msgs = _translate_input_items(items)
        assert msgs == [{"role": "assistant", "content": "a\nb"}]

    def test_dict_output_is_json_serialised(self):
        msgs = _translate_input_items(
            [{"type": "function_call_output", "call_id": "c1", "output": {"k": 2}}]
        )
        assert msgs[0]["content"][0]["content"] == '{"k": 2}'


class TestResponseParsing:
    def test_extract_text_concatenates_text_blocks(self):
        raw = {"content": [{"type": "text", "text": "line one"}, {"type": "text", "text": "line two"}]}
        assert _extract_output_text(raw) == "line one\nline two"

    def test_extract_tool_calls_maps_tool_use_blocks(self):
        raw = {
            "content": [
                {"type": "tool_use", "id": "tu_1", "name": "run", "input": {"node": "OUT"}},
            ]
        }
        calls = _extract_tool_calls(raw)
        assert calls == [ProviderToolCall(call_id="tu_1", name="run", arguments={"node": "OUT"})]

    def test_tool_use_only_turn_has_empty_text(self):
        raw = {"content": [{"type": "tool_use", "id": "tu_1", "name": "run", "input": {}}]}
        assert _extract_output_text(raw) == ""

    def test_tool_use_block_without_input_yields_empty_args(self):
        raw = {"content": [{"type": "tool_use", "id": "tu_1", "name": "run"}]}
        assert _extract_tool_calls(raw)[0].arguments == {}


class TestMockHooks:
    def test_complete_honors_mock_response(self, monkeypatch):
        monkeypatch.setenv("AISWMM_ANTHROPIC_MOCK_RESPONSE", "mocked text")
        # No api_key needed on the mock path.
        result = AnthropicProvider(model="claude-sonnet-4-6", api_key=None).complete(
            system_prompt="s", prompt="p"
        )
        assert isinstance(result, ProviderResult)
        assert result.text == "mocked text"
        assert result.model == "claude-sonnet-4-6"

    def test_respond_with_tools_honors_mock_tool_calls_then_consumes(self, monkeypatch):
        monkeypatch.setenv(
            "AISWMM_ANTHROPIC_MOCK_TOOL_CALLS",
            json.dumps([{"name": "doctor", "arguments": {"x": 1}}]),
        )
        provider = AnthropicProvider(model="claude-sonnet-4-6", api_key=None)
        first = provider.respond_with_tools(system_prompt="s", input_items=[], tools=[])
        assert isinstance(first, ProviderToolResponse)
        assert [c.name for c in first.tool_calls] == ["doctor"]
        assert first.tool_calls[0].arguments == {"x": 1}
        # The mock tool-calls fire once; the second turn falls through to
        # the missing-key error (no scripted second response).
        with pytest.raises(RuntimeError):
            provider.respond_with_tools(system_prompt="s", input_items=[], tools=[])

    def test_respond_with_tools_mock_response_returns_text(self, monkeypatch):
        monkeypatch.setenv("AISWMM_ANTHROPIC_MOCK_RESPONSE", "final answer")
        provider = AnthropicProvider(model="claude-sonnet-4-6", api_key=None)
        out = provider.respond_with_tools(system_prompt="s", input_items=[], tools=[])
        assert out.text == "final answer"
        assert out.tool_calls == []


class TestMissingKey:
    def test_complete_without_key_raises_naming_anthropic_api_key(self, monkeypatch):
        monkeypatch.delenv("AISWMM_ANTHROPIC_MOCK_RESPONSE", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError) as exc:
            AnthropicProvider(model="claude-sonnet-4-6", api_key=None).complete(
                system_prompt="s", prompt="p"
            )
        assert "ANTHROPIC_API_KEY" in str(exc.value)

    def test_respond_with_tools_without_key_raises(self, monkeypatch):
        monkeypatch.delenv("AISWMM_ANTHROPIC_MOCK_RESPONSE", raising=False)
        monkeypatch.delenv("AISWMM_ANTHROPIC_MOCK_TOOL_CALLS", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError) as exc:
            AnthropicProvider(model="claude-sonnet-4-6", api_key=None).respond_with_tools(
                system_prompt="s", input_items=[], tools=[]
            )
        assert "ANTHROPIC_API_KEY" in str(exc.value)


class TestRequestBody:
    """The single HTTP path: assert the wire body without a socket."""

    def test_complete_sends_required_fields(self, monkeypatch):
        captured = {}

        def _fake_post(self, payload):
            captured["payload"] = payload
            return {"id": "msg_1", "content": [{"type": "text", "text": "ok"}]}

        monkeypatch.setattr(AnthropicProvider, "_post", _fake_post)
        result = _provider().complete(system_prompt="be brief", prompt="hi")
        body = captured["payload"]
        assert body["model"] == "claude-sonnet-4-6"
        assert body["max_tokens"] == ANTHROPIC_MAX_TOKENS
        assert body["system"] == "be brief"
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert result.text == "ok"

    def test_respond_with_tools_includes_translated_tools_and_messages(self, monkeypatch):
        captured = {}

        def _fake_post(self, payload):
            captured["payload"] = payload
            return {
                "id": "msg_2",
                "content": [{"type": "tool_use", "id": "tu_9", "name": "doctor", "input": {}}],
            }

        monkeypatch.setattr(AnthropicProvider, "_post", _fake_post)
        out = _provider().respond_with_tools(
            system_prompt="s",
            input_items=[{"role": "user", "content": "go"}],
            tools=[{"type": "function", "name": "doctor", "parameters": {"type": "object"}}],
        )
        body = captured["payload"]
        assert body["max_tokens"] == ANTHROPIC_MAX_TOKENS
        assert body["tools"] == [
            {"name": "doctor", "description": "", "input_schema": {"type": "object"}}
        ]
        assert body["messages"] == [{"role": "user", "content": "go"}]
        # response_id comes from the message id; the tool_use is surfaced.
        assert out.response_id == "msg_2"
        assert [c.name for c in out.tool_calls] == ["doctor"]

    def test_no_tools_omits_tools_key(self, monkeypatch):
        captured = {}

        def _fake_post(self, payload):
            captured["payload"] = payload
            return {"id": "m", "content": [{"type": "text", "text": "x"}]}

        monkeypatch.setattr(AnthropicProvider, "_post", _fake_post)
        _provider().respond_with_tools(system_prompt="s", input_items=[], tools=[])
        assert "tools" not in captured["payload"]


class TestWireConstants:
    def test_endpoint_and_version_constants(self):
        assert ANTHROPIC_MESSAGES_URL == "https://api.anthropic.com/v1/messages"
        assert ANTHROPIC_VERSION == "2023-06-01"
        assert isinstance(ANTHROPIC_MAX_TOKENS, int) and ANTHROPIC_MAX_TOKENS > 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
