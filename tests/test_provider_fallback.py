"""FallbackProvider: when it engages, when it must not, and what it replays."""
from __future__ import annotations

import json
from typing import Any

import pytest

from agentic_swmm.providers._http import (
    MissingCredentialsError,
    ProviderConnectionError,
    ProviderHTTPError,
)
from agentic_swmm.providers.base import ProviderResult, ProviderToolCall, ProviderToolResponse
from agentic_swmm.providers.fallback import FallbackProvider


class ScriptedProvider:
    """ChatProvider double: raises scripted errors, records what it saw."""

    def __init__(self, *, errors: list[Exception] | None = None, name: str = "fake"):
        self.model = f"{name}-model"
        self._errors = list(errors or [])
        self.tool_requests: list[dict[str, Any]] = []
        self.completes = 0

    def _maybe_raise(self):
        if self._errors:
            raise self._errors.pop(0)

    def complete(self, *, system_prompt, prompt):
        self._maybe_raise()
        self.completes += 1
        return ProviderResult(text="ok", model=self.model, raw={})

    def respond_with_tools(self, *, system_prompt, input_items, tools, previous_response_id=None):
        self._maybe_raise()
        self.tool_requests.append(
            {"input_items": list(input_items), "previous_response_id": previous_response_id}
        )
        return ProviderToolResponse(
            text="answer",
            model=self.model,
            response_id=f"r{len(self.tool_requests)}",
            tool_calls=[],
            raw={},
        )


def _pair(primary_errors=None):
    primary = ScriptedProvider(errors=primary_errors, name="primary")
    fallback = ScriptedProvider(name="fallback")
    wrapped = FallbackProvider(
        primary=primary, fallback=fallback, primary_name="openai", fallback_name="ollama"
    )
    return primary, fallback, wrapped


ENGAGING = [
    MissingCredentialsError("no key"),
    ProviderConnectionError("refused"),
    ProviderHTTPError("auth", status=401),
    ProviderHTTPError("quota", status=429),
    ProviderHTTPError("down", status=503),
]

NOT_ENGAGING = [
    ProviderHTTPError("bad request", status=400),
    ProviderHTTPError("bad model", status=404),
    RuntimeError("some other bug"),
]


@pytest.mark.parametrize("error", ENGAGING, ids=lambda e: str(e))
def test_engages_on_outage_class_errors(error, capsys):
    primary, fallback, wrapped = _pair([error])
    result = wrapped.complete(system_prompt="s", prompt="p")
    assert result.text == "ok"
    assert fallback.completes == 1
    err = capsys.readouterr().err
    assert "falling back to 'ollama'" in err


@pytest.mark.parametrize("error", NOT_ENGAGING, ids=lambda e: str(e))
def test_request_bugs_surface_instead_of_falling_back(error):
    primary, fallback, wrapped = _pair([error])
    with pytest.raises(type(error)):
        wrapped.complete(system_prompt="s", prompt="p")
    assert fallback.completes == 0


def test_switch_is_sticky():
    primary, fallback, wrapped = _pair([ProviderHTTPError("down", status=500)])
    wrapped.complete(system_prompt="s", prompt="p")
    wrapped.complete(system_prompt="s", prompt="p")
    assert fallback.completes == 2
    assert primary.completes == 0


def test_mid_conversation_switch_replays_full_context():
    """Turn 1 succeeds on primary (with a tool call), turn 2's primary
    failure hands the fallback the WHOLE conversation, including the
    synthesized assistant turn a stateful primary never resent."""
    primary = ScriptedProvider(name="primary")
    fallback = ScriptedProvider(name="fallback")
    wrapped = FallbackProvider(
        primary=primary, fallback=fallback, primary_name="openai", fallback_name="ollama"
    )

    # Turn 1: primary answers with a tool call.
    primary_response = ProviderToolResponse(
        text="thinking",
        model="primary-model",
        response_id="p1",
        tool_calls=[ProviderToolCall(call_id="c1", name="run_swmm", arguments={"inp": "a.inp"})],
        raw={},
    )
    primary.respond_with_tools = lambda **kw: primary_response  # type: ignore[assignment]
    first = wrapped.respond_with_tools(
        system_prompt="s", input_items=[{"role": "user", "content": "goal"}], tools=[]
    )
    assert first.response_id == "p1"

    # Turn 2: primary dies with a quota error.
    def dying(**kw):
        raise ProviderHTTPError("quota", status=429)

    primary.respond_with_tools = dying  # type: ignore[assignment]
    second = wrapped.respond_with_tools(
        system_prompt="s",
        input_items=[{"type": "function_call_output", "call_id": "c1", "output": "{}"}],
        tools=[],
        previous_response_id="p1",
    )
    assert second.text == "answer"

    replay = fallback.tool_requests[0]
    assert replay["previous_response_id"] is None  # fresh session for fallback
    items = replay["input_items"]
    assert items[0] == {"role": "user", "content": "goal"}
    assert items[1] == {"role": "assistant", "content": "thinking"}
    assert items[2]["type"] == "function_call"
    assert items[2]["call_id"] == "c1"
    assert json.loads(items[2]["arguments"]) == {"inp": "a.inp"}
    assert items[3] == {"type": "function_call_output", "call_id": "c1", "output": "{}"}
