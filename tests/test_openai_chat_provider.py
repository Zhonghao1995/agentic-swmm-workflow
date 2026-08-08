"""OpenAIChatProvider: chat/completions wire adaptation (ADR-0008).

The provider adapts the Responses-flavoured ``ChatProvider`` protocol
onto stateless chat/completions the same way the Anthropic provider
adapts Messages: local history, per-turn delta in, assistant turn
recorded after each response. These tests monkeypatch the shared HTTP
helper so no network is touched.
"""
from __future__ import annotations

import json

import pytest

from agentic_swmm.providers import openai_chat
from agentic_swmm.providers._http import MissingCredentialsError
from agentic_swmm.providers.openai_chat import OpenAIChatProvider


AISWMM_TOOL = {
    "type": "function",
    "name": "run_swmm",
    "description": "Run a SWMM simulation.",
    "parameters": {"type": "object", "properties": {"inp": {"type": "string"}}},
}


@pytest.fixture()
def capture(monkeypatch):
    """Replace the HTTP helper; capture payloads, feed canned responses."""
    calls: list[dict] = []
    responses: list[dict] = []

    def fake_post(request, **kwargs):
        calls.append(json.loads(request.data.decode("utf-8")))
        return responses.pop(0)

    monkeypatch.setattr(openai_chat, "post_json_with_retry", fake_post)
    monkeypatch.delenv("AISWMM_CHAT_MOCK_RESPONSE", raising=False)
    monkeypatch.delenv("AISWMM_CHAT_MOCK_TOOL_CALLS", raising=False)
    return calls, responses


def _provider(**kw):
    defaults = dict(model="m1", base_url="http://gw/v1", api_key="k")
    defaults.update(kw)
    return OpenAIChatProvider(**defaults)


def _chat_response(*, content=None, tool_calls=None, rid="resp-1"):
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"id": rid, "choices": [{"message": message}]}


class TestToolTranslation:
    def test_tools_nest_into_function_shape(self, capture):
        calls, responses = capture
        responses.append(_chat_response(content="ok"))
        provider = _provider()
        provider.respond_with_tools(
            system_prompt="sys", input_items=[{"role": "user", "content": "go"}], tools=[AISWMM_TOOL]
        )
        sent = calls[0]
        assert sent["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "run_swmm",
                    "description": "Run a SWMM simulation.",
                    "parameters": AISWMM_TOOL["parameters"],
                },
            }
        ]
        assert sent["messages"][0] == {"role": "system", "content": "sys"}
        assert sent["messages"][1] == {"role": "user", "content": "go"}
        assert sent["model"] == "m1"

    def test_endpoint_derived_from_base_url(self, capture):
        provider = _provider(base_url="http://gw:9999/v1/")
        assert provider.base_url == "http://gw:9999/v1"


class TestToolCallRoundTrip:
    def test_tool_calls_parse_and_outputs_translate(self, capture):
        calls, responses = capture
        responses.append(
            _chat_response(
                content=None,
                tool_calls=[
                    {
                        "id": "call-7",
                        "type": "function",
                        "function": {"name": "run_swmm", "arguments": '{"inp": "a.inp"}'},
                    }
                ],
            )
        )
        responses.append(_chat_response(content="done", rid="resp-2"))
        provider = _provider()

        first = provider.respond_with_tools(
            system_prompt="sys", input_items=[{"role": "user", "content": "run it"}], tools=[AISWMM_TOOL]
        )
        assert [ (c.call_id, c.name, c.arguments) for c in first.tool_calls ] == [
            ("call-7", "run_swmm", {"inp": "a.inp"})
        ]
        assert first.text == ""

        second = provider.respond_with_tools(
            system_prompt="sys",
            input_items=[
                {"type": "function_call_output", "call_id": "call-7", "output": '{"status": "ok"}'}
            ],
            tools=[AISWMM_TOOL],
            previous_response_id=first.response_id,
        )
        assert second.text == "done"

        sent = calls[1]["messages"]
        # system + user + assistant(tool_calls) + tool result, in order.
        assert sent[0]["role"] == "system"
        assert sent[1] == {"role": "user", "content": "run it"}
        assert sent[2]["role"] == "assistant"
        assert sent[2]["tool_calls"][0]["id"] == "call-7"
        assert sent[3] == {
            "role": "tool",
            "tool_call_id": "call-7",
            "content": '{"status": "ok"}',
        }

    def test_history_resets_when_previous_response_id_is_none(self, capture):
        calls, responses = capture
        responses.append(_chat_response(content="one"))
        responses.append(_chat_response(content="two"))
        provider = _provider()
        provider.respond_with_tools(
            system_prompt="s", input_items=[{"role": "user", "content": "a"}], tools=[]
        )
        provider.respond_with_tools(
            system_prompt="s", input_items=[{"role": "user", "content": "b"}], tools=[]
        )
        # Second call passed previous_response_id=None -> history reset:
        # only system + the new user message.
        assert [m.get("content") for m in calls[1]["messages"]] == ["s", "b"]


class TestReadiness:
    def test_missing_key_raises_actionable_error(self, capture):
        provider = _provider(api_key=None, keyless=False, missing_key_error="KEY missing. Run `aiswmm login groq`.")
        with pytest.raises(MissingCredentialsError) as exc:
            provider.complete(system_prompt="s", prompt="p")
        assert "aiswmm login" in str(exc.value)

    def test_keyless_route_sends_without_auth_header(self, capture, monkeypatch):
        seen_headers = {}

        def fake_post(request, **kwargs):
            seen_headers.update(request.headers)
            return _chat_response(content="hi")

        monkeypatch.setattr(openai_chat, "post_json_with_retry", fake_post)
        provider = _provider(api_key=None, keyless=True)
        result = provider.complete(system_prompt="s", prompt="p")
        assert result.text == "hi"
        assert not any(k.lower() == "authorization" for k in seen_headers)

    def test_missing_base_url_raises(self, capture):
        provider = _provider(base_url="", api_key="k")
        with pytest.raises(RuntimeError) as exc:
            provider.complete(system_prompt="s", prompt="p")
        assert "base URL" in str(exc.value)


class TestMockSeam:
    def test_mock_response_short_circuits(self, monkeypatch):
        monkeypatch.setenv("AISWMM_CHAT_MOCK_RESPONSE", "canned")
        provider = _provider(api_key=None, keyless=False)
        assert provider.complete(system_prompt="s", prompt="p").text == "canned"

    def test_mock_tool_calls_then_response(self, monkeypatch):
        monkeypatch.setenv(
            "AISWMM_CHAT_MOCK_TOOL_CALLS",
            json.dumps([{"name": "run_swmm", "arguments": {"inp": "x.inp"}}]),
        )
        monkeypatch.setenv("AISWMM_CHAT_MOCK_RESPONSE", "after")
        provider = _provider(api_key=None, keyless=False)
        first = provider.respond_with_tools(system_prompt="s", input_items=[], tools=[])
        assert first.tool_calls[0].name == "run_swmm"
        second = provider.respond_with_tools(system_prompt="s", input_items=[], tools=[])
        assert second.text == "after"
        assert second.tool_calls == []
