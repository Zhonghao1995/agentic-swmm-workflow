"""ClaudeSDKProvider unit tests (PRD-09).

Every test mocks ``claude_agent_sdk`` via the ``mock_claude_sdk_module``
fixture in ``tests/conftest.py``. The stub exposes:

- ``stub.script([...])`` — queue the messages the next ``query()``
  yields.
- ``stub.script_error(exc)`` — make the next ``query()`` raise.
- ``stub.last_call`` — inspect the prompt and options the provider
  passed to ``query()`` on its most recent call.
"""
from __future__ import annotations

import sys

import pytest

from agentic_swmm.providers.base import (
    ProviderResult,
    ProviderToolCall,
    ProviderToolResponse,
)


def _provider():
    """Build a fresh ``ClaudeSDKProvider`` with a short timeout so
    a stuck test doesn't hang the suite."""
    from agentic_swmm.providers.claude_sdk_api import ClaudeSDKProvider

    return ClaudeSDKProvider(model="claude-sonnet-4-5-20250929", timeout=5)


class TestComplete:
    def test_returns_provider_result_with_text(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [
                sdk.AssistantMessage(
                    content=[sdk.TextBlock(text="hello world")],
                    model="claude-sonnet-4-5",
                ),
                sdk.ResultMessage(session_id="sess_1"),
            ]
        )

        result = _provider().complete(system_prompt="be brief", prompt="say hi")

        assert isinstance(result, ProviderResult)
        assert result.text == "hello world"
        assert result.model == "claude-sonnet-4-5-20250929"

    def test_concatenates_multiple_text_blocks(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [
                sdk.AssistantMessage(
                    content=[
                        sdk.TextBlock(text="line one"),
                        sdk.TextBlock(text="line two"),
                    ]
                ),
            ]
        )

        result = _provider().complete(system_prompt="x", prompt="y")

        assert "line one" in result.text
        assert "line two" in result.text

    def test_passes_system_prompt_through_options(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [sdk.AssistantMessage(content=[sdk.TextBlock(text="ok")])]
        )

        _provider().complete(system_prompt="be brief and audit-friendly", prompt="hi")

        opts = mock_claude_sdk_module.last_call["options"]
        assert opts.system_prompt == "be brief and audit-friendly"
        assert opts.model == "claude-sonnet-4-5-20250929"


class TestErrorMapping:
    def test_cli_not_found_becomes_runtime_error(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script_error(sdk.CLINotFoundError("no claude binary"))

        with pytest.raises(RuntimeError) as exc_info:
            _provider().complete(system_prompt="x", prompt="y")

        assert "claude" in str(exc_info.value).lower()
        assert "https://docs.claude.com" in str(exc_info.value)

    def test_cli_connection_error_mentions_login(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script_error(sdk.CLIConnectionError("connect refused"))

        with pytest.raises(RuntimeError) as exc_info:
            _provider().complete(system_prompt="x", prompt="y")

        assert "claude login" in str(exc_info.value)

    def test_process_error_surfaces_stderr(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script_error(
            sdk.ProcessError("boom", stderr="auth_token expired")
        )

        with pytest.raises(RuntimeError) as exc_info:
            _provider().complete(system_prompt="x", prompt="y")

        assert "auth_token expired" in str(exc_info.value)

    def test_json_decode_error_suggests_cli_upgrade(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script_error(sdk.CLIJSONDecodeError("bad json"))

        with pytest.raises(RuntimeError) as exc_info:
            _provider().complete(system_prompt="x", prompt="y")

        assert "transport corruption" in str(exc_info.value).lower()

    def test_generic_sdk_error_wraps_to_runtime_error(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script_error(sdk.ClaudeSDKError("unspecified"))

        with pytest.raises(RuntimeError) as exc_info:
            _provider().complete(system_prompt="x", prompt="y")

        assert "Claude Agent SDK error" in str(exc_info.value)


class TestRespondWithTools:
    def test_tool_use_block_adapts_to_provider_tool_call(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [
                sdk.AssistantMessage(
                    content=[
                        sdk.ToolUseBlock(
                            id="tool_use_xyz",
                            name="read_file",
                            input={"path": "examples/tecnopolo.inp"},
                        )
                    ]
                ),
                sdk.ResultMessage(session_id="sess_2"),
            ]
        )

        response = _provider().respond_with_tools(
            system_prompt="x",
            input_items=[{"role": "user", "content": "read the file"}],
            tools=[
                {
                    "type": "function",
                    "name": "read_file",
                    "parameters": {"type": "object"},
                }
            ],
        )

        assert isinstance(response, ProviderToolResponse)
        assert len(response.tool_calls) == 1
        call = response.tool_calls[0]
        assert isinstance(call, ProviderToolCall)
        assert call.call_id == "tool_use_xyz"
        assert call.name == "read_file"
        assert call.arguments == {"path": "examples/tecnopolo.inp"}

    def test_text_only_response_returns_empty_tool_calls(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [
                sdk.AssistantMessage(
                    content=[sdk.TextBlock(text="no tools needed; done")]
                ),
            ]
        )

        response = _provider().respond_with_tools(
            system_prompt="x",
            input_items=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert response.tool_calls == []
        assert "done" in response.text

    def test_multiple_tool_calls_preserve_order(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [
                sdk.AssistantMessage(
                    content=[
                        sdk.ToolUseBlock(id="a", name="list_files", input={}),
                        sdk.ToolUseBlock(id="b", name="read_file", input={"path": "x"}),
                    ]
                ),
            ]
        )

        response = _provider().respond_with_tools(
            system_prompt="x",
            input_items=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert [c.call_id for c in response.tool_calls] == ["a", "b"]
        assert [c.name for c in response.tool_calls] == ["list_files", "read_file"]

    def test_previous_response_id_is_accepted_and_ignored(self, mock_claude_sdk_module):
        """Protocol parity: the SDK has no server-side response state,
        so ``previous_response_id`` is a no-op. The call must still
        succeed with the kwarg supplied."""
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [sdk.AssistantMessage(content=[sdk.TextBlock(text="ok")])]
        )

        response = _provider().respond_with_tools(
            system_prompt="x",
            input_items=[{"role": "user", "content": "hi"}],
            tools=[],
            previous_response_id="resp_abc123",
        )

        assert response is not None

    def test_tool_schema_populates_allowed_tools(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [sdk.AssistantMessage(content=[sdk.TextBlock(text="ok")])]
        )

        _provider().respond_with_tools(
            system_prompt="x",
            input_items=[{"role": "user", "content": "hi"}],
            tools=[
                {"type": "function", "name": "read_file", "parameters": {}},
                {"type": "function", "name": "list_files", "parameters": {}},
                {"type": "function", "parameters": {}},  # missing name; skipped
            ],
        )

        opts = mock_claude_sdk_module.last_call["options"]
        assert opts.allowed_tools == ["read_file", "list_files"]

    def test_empty_tools_list_yields_empty_allowed_tools(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [sdk.AssistantMessage(content=[sdk.TextBlock(text="ok")])]
        )

        _provider().respond_with_tools(
            system_prompt="x",
            input_items=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        opts = mock_claude_sdk_module.last_call["options"]
        assert opts.allowed_tools == []

    def test_multi_turn_history_flattens_into_prompt(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [sdk.AssistantMessage(content=[sdk.TextBlock(text="ack")])]
        )

        _provider().respond_with_tools(
            system_prompt="x",
            input_items=[
                {"role": "user", "content": "first message"},
                {"role": "assistant", "content": "first reply"},
                {"role": "user", "content": "second message"},
            ],
            tools=[],
        )

        prompt = mock_claude_sdk_module.last_call["prompt"]
        assert "first message" in prompt
        assert "first reply" in prompt
        assert "second message" in prompt
        # Roles tagged in order:
        assert prompt.index("first message") < prompt.index("first reply") < prompt.index(
            "second message"
        )

    def test_function_call_items_become_tagged_blocks(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [sdk.AssistantMessage(content=[sdk.TextBlock(text="ack")])]
        )

        _provider().respond_with_tools(
            system_prompt="x",
            input_items=[
                {"role": "user", "content": "read it"},
                {"type": "function_call", "name": "read_file", "arguments": '{"path": "a"}'},
                {"type": "function_call_output", "output": "file contents here"},
            ],
            tools=[],
        )

        prompt = mock_claude_sdk_module.last_call["prompt"]
        assert "read_file" in prompt
        assert "file contents here" in prompt

    def test_session_id_surfaces_as_response_id(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [
                sdk.AssistantMessage(
                    content=[sdk.TextBlock(text="ok")],
                    session_id="sess_first",
                ),
                sdk.ResultMessage(session_id="sess_first", total_cost_usd=0.01),
            ]
        )

        response = _provider().respond_with_tools(
            system_prompt="x",
            input_items=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert response.response_id == "sess_first"

    def test_result_message_cost_captured_in_raw(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [
                sdk.AssistantMessage(content=[sdk.TextBlock(text="done")]),
                sdk.ResultMessage(session_id="sess_x", total_cost_usd=0.0042),
            ]
        )

        response = _provider().respond_with_tools(
            system_prompt="x",
            input_items=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        result_msgs = [m for m in response.raw["messages"] if m["type"] == "result"]
        assert len(result_msgs) == 1
        assert result_msgs[0]["total_cost_usd"] == 0.0042


class TestRateLimitHandling:
    """PRD-09 S8 — a rate-limited turn degrades visibly, not silently."""

    def test_rate_limit_event_surfaces_hint_in_text(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [
                sdk.RateLimitEvent(
                    rate_limit_info=sdk.RateLimitInfo(status="rate_limited")
                ),
            ]
        )

        response = _provider().respond_with_tools(
            system_prompt="x",
            input_items=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert "rate limit" in response.text.lower()
        assert "--provider openai" in response.text
        assert response.raw.get("rate_limited") is True

    def test_rate_limit_event_captured_in_raw_messages(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [
                sdk.RateLimitEvent(
                    rate_limit_info=sdk.RateLimitInfo(status="rate_limited")
                ),
            ]
        )

        response = _provider().respond_with_tools(
            system_prompt="x",
            input_items=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        rate_rows = [m for m in response.raw["messages"] if m["type"] == "rate_limit"]
        assert len(rate_rows) == 1

    def test_complete_surfaces_rate_limit_hint(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [
                sdk.RateLimitEvent(
                    rate_limit_info=sdk.RateLimitInfo(status="rate_limited")
                ),
            ]
        )

        result = _provider().complete(system_prompt="x", prompt="y")

        assert "rate limit" in result.text.lower()
        assert result.raw.get("rate_limited") is True

    def test_text_response_unaffected_by_absent_rate_limit(self, mock_claude_sdk_module):
        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [sdk.AssistantMessage(content=[sdk.TextBlock(text="normal answer")])]
        )

        response = _provider().respond_with_tools(
            system_prompt="x",
            input_items=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert response.text == "normal answer"
        assert "rate_limited" not in response.raw


class TestImportHygiene:
    def test_provider_module_does_not_import_sdk_eagerly(self, monkeypatch):
        """Importing the provider module must not pull
        ``claude_agent_sdk`` into ``sys.modules``. The SDK is only
        touched inside ``_load_sdk`` at first runtime call."""
        for name in [
            "agentic_swmm.providers.claude_sdk_api",
            "claude_agent_sdk",
        ]:
            sys.modules.pop(name, None)

        import agentic_swmm.providers.claude_sdk_api  # noqa: F401

        assert "claude_agent_sdk" not in sys.modules


class TestAsyncBridge:
    def test_works_under_pytest_asyncio_event_loop(self, mock_claude_sdk_module):
        """The sync wrapper must not collide with an outer event loop.
        We simulate one by entering ``asyncio.run()`` and calling the
        provider from inside it via ``run_in_executor`` — the daemon
        thread starts its own loop and joins cleanly."""
        import asyncio

        sdk = sys.modules["claude_agent_sdk"]
        mock_claude_sdk_module.script(
            [sdk.AssistantMessage(content=[sdk.TextBlock(text="from inside loop")])]
        )

        provider = _provider()

        async def _outer():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                lambda: provider.complete(system_prompt="x", prompt="y"),
            )

        result = asyncio.run(_outer())
        assert result.text == "from inside loop"


class TestLiveSDK:
    """Live-SDK smoke tests. Skipped unless ``AISWMM_RUN_LIVE_CLAUDE=1``
    is exported — gates the test on opting into real subscription
    quota consumption."""

    @pytest.mark.skipif(
        not __import__("os").environ.get("AISWMM_RUN_LIVE_CLAUDE"),
        reason="set AISWMM_RUN_LIVE_CLAUDE=1 to exercise the real SDK",
    )
    def test_live_complete_smoke(self):  # pragma: no cover - opt-in only
        from agentic_swmm.providers.claude_sdk_api import ClaudeSDKProvider

        provider = ClaudeSDKProvider(
            model="claude-sonnet-4-5-20250929", timeout=60
        )
        result = provider.complete(
            system_prompt="reply with the word 'ack' only",
            prompt="ack",
        )
        assert isinstance(result.text, str)
