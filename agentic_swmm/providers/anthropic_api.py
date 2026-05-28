"""Anthropic Messages-API provider (raw HTTP, no SDK).

This is the opt-in second backend (``--provider anthropic``). It speaks
the public Anthropic Messages API directly over pure-stdlib ``urllib``
— no ``anthropic`` package, no ``claude-agent-sdk``, no subprocess —
so it adds zero dependencies and uses robust, standard function
calling (the planner's registered tools are advertised verbatim and
the model can only emit those tools).

It mirrors :class:`agentic_swmm.providers.openai_api.OpenAIProvider`'s
public surface (``complete`` / ``respond_with_tools`` plus the two
mock-response env hooks) so the factory can swap one for the other and
the planner / runtime / tool-registry surfaces never learn which
backend is live.

### Wire contract

* Endpoint ``https://api.anthropic.com/v1/messages``.
* Headers: ``x-api-key: $ANTHROPIC_API_KEY``,
  ``anthropic-version: 2023-06-01``, ``content-type: application/json``.
* Body: ``model``, ``max_tokens`` (REQUIRED by this API —
  :data:`ANTHROPIC_MAX_TOKENS`), ``system`` (the aiswmm system prompt),
  ``messages``, and ``tools`` when present.

### Tool-schema translation

aiswmm advertises OpenAI-Responses-shaped tool descriptors
``{"type": "function", "name": ..., "parameters": {...}}``. Anthropic
wants ``{"name": ..., "description": ..., "input_schema": {...}}`` —
:func:`_translate_tools` renames ``parameters`` to ``input_schema`` and
lifts any ``description`` (defaulting to an empty string).

### ``input_items`` → ``messages`` translation

aiswmm passes OpenAI-Responses-style ``input_items``: role-tagged text
items plus ``function_call`` / ``function_call_output`` items between
turns. :func:`_translate_input_items` maps those onto Anthropic
``messages``:

* a user / assistant text item → ``{"role", "content": "<text>"}``;
* a ``function_call`` → an assistant message carrying a ``tool_use``
  content block (``id`` / ``name`` / ``input``);
* a ``function_call_output`` → a user message carrying a
  ``tool_result`` block keyed by ``tool_use_id`` (= the matching
  ``call_id``).

There is no server-side turn state, so the full history is passed on
every call and ``previous_response_id`` is accepted for Protocol parity
and ignored.

### Response parsing

The Messages API returns a ``content`` array of typed blocks.
:func:`_extract_output_text` concatenates every ``text`` block;
:func:`_extract_tool_calls` adapts every ``tool_use`` block to
:class:`ProviderToolCall(call_id=block["id"], name=block["name"],
arguments=block["input"])`. ``response_id`` is the message ``id``.

### Mock hooks

Mirrors openai_api.py: ``AISWMM_ANTHROPIC_MOCK_RESPONSE`` short-circuits
both methods with a canned text answer, and
``AISWMM_ANTHROPIC_MOCK_TOOL_CALLS`` (a JSON list) makes the first
``respond_with_tools`` turn emit scripted tool calls. Both let the test
suite run with no network and no API key.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from agentic_swmm.providers.base import ProviderResult, ProviderToolCall, ProviderToolResponse


ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
# The Messages API requires ``max_tokens``. 4096 is a generous ceiling
# for the planner's short tool-selection / final-answer turns; it caps
# the response, not the prompt.
ANTHROPIC_MAX_TOKENS = 4096
# Pinned per the public Messages API. Sent as the ``anthropic-version``
# header on every request.
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    def __init__(self, *, model: str, api_key: str | None = None, timeout: int = 120) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.timeout = timeout
        self._mock_tool_calls_consumed = False

    def complete(self, *, system_prompt: str, prompt: str) -> ProviderResult:
        mock_response = os.environ.get("AISWMM_ANTHROPIC_MOCK_RESPONSE")
        if mock_response is not None:
            return ProviderResult(text=mock_response, model=self.model, raw={"mock": True})
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Run `aiswmm login --anthropic` to store a key."
            )

        payload = {
            "model": self.model,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "system": system_prompt,
            "messages": [{"role": "user", "content": prompt}],
        }
        raw = self._post(payload)
        return ProviderResult(text=_extract_output_text(raw), model=self.model, raw=raw)

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        mock_tool_calls = os.environ.get("AISWMM_ANTHROPIC_MOCK_TOOL_CALLS")
        if mock_tool_calls is not None and not self._mock_tool_calls_consumed:
            self._mock_tool_calls_consumed = True
            calls = _parse_mock_tool_calls(mock_tool_calls)
            raw = {"mock": True, "content": [_mock_call_payload(call) for call in calls]}
            return ProviderToolResponse(text="", model=self.model, response_id="mock-response-1", tool_calls=calls, raw=raw)
        mock_response = os.environ.get("AISWMM_ANTHROPIC_MOCK_RESPONSE")
        if mock_response is not None:
            return ProviderToolResponse(
                text=mock_response,
                model=self.model,
                response_id="mock-response-final",
                tool_calls=[],
                raw={"mock": True, "output_text": mock_response},
            )
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Set it or use "
                "AISWMM_ANTHROPIC_MOCK_TOOL_CALLS for local tests."
            )

        # No server-side state on the Messages API: pass the full
        # history every turn. ``previous_response_id`` is accepted for
        # Protocol parity with the OpenAI provider and ignored.
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "system": system_prompt,
            "messages": _translate_input_items(input_items),
        }
        translated_tools = _translate_tools(tools)
        if translated_tools:
            payload["tools"] = translated_tools
        raw = self._post(payload)
        return ProviderToolResponse(
            text=_extract_output_text(raw),
            model=self.model,
            response_id=raw.get("id") if isinstance(raw.get("id"), str) else None,
            tool_calls=_extract_tool_calls(raw),
            raw=raw,
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST ``payload`` to the Messages API; return the parsed JSON.

        Wraps HTTP / URL errors as ``RuntimeError`` with the status code
        and response body so a 4xx (bad key, bad model) surfaces a clear,
        actionable message instead of a raw traceback.
        """
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            ANTHROPIC_MESSAGES_URL,
            data=data,
            method="POST",
            headers={
                "x-api-key": self.api_key or "",
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anthropic API request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Anthropic API request failed: {exc.reason}") from exc


def _translate_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map aiswmm OpenAI-shaped tool descriptors to Anthropic's shape.

    aiswmm tools are ``{"type": "function", "name", "parameters"}``;
    Anthropic wants ``{"name", "description", "input_schema"}``. The
    ``parameters`` JSON-Schema object becomes ``input_schema`` verbatim;
    ``description`` is lifted when present (else an empty string).
    Descriptors without a string ``name`` are skipped rather than
    raising — keeps the provider resilient to upstream schema drift.
    """
    translated: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        input_schema = tool.get("parameters")
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        translated.append(
            {
                "name": name,
                "description": tool.get("description") or "",
                "input_schema": input_schema,
            }
        )
    return translated


def _translate_input_items(input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map OpenAI-Responses ``input_items`` onto Anthropic ``messages``.

    Each item becomes one Anthropic message:

    * ``function_call`` → assistant message with a ``tool_use`` block
      (``id`` = the item's ``call_id``/``id``, ``name``, ``input`` =
      the parsed ``arguments``);
    * ``function_call_output`` → user message with a ``tool_result``
      block keyed by ``tool_use_id`` (= the matching ``call_id``);
    * any other item → a message with ``role`` (default ``"user"``) and
      its text content. Responses-style rich-content lists are flattened
      to their concatenated ``text`` blocks.

    Items that are not dicts are skipped.
    """
    messages: list[dict[str, Any]] = []
    for item in input_items or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "function_call":
            call_id = item.get("call_id") or item.get("id") or ""
            messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": str(call_id),
                            "name": str(item.get("name") or ""),
                            "input": _parse_arguments(item.get("arguments", "{}")),
                        }
                    ],
                }
            )
            continue
        if item_type == "function_call_output":
            call_id = item.get("call_id") or item.get("id") or ""
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(call_id),
                            "content": _coerce_output_text(item.get("output", "")),
                        }
                    ],
                }
            )
            continue
        role = item.get("role", "user")
        if role not in ("user", "assistant"):
            # Anthropic only accepts user/assistant message roles; a
            # stray system/other item is folded into the user turn.
            role = "user"
        messages.append({"role": role, "content": _coerce_content_text(item.get("content", ""))})
    return messages


def _coerce_content_text(content: Any) -> str:
    """Flatten a Responses-style ``content`` value to a plain string.

    A bare string passes through. A list of rich-content blocks has its
    ``text`` fields concatenated (newline-joined). Anything else is
    stringified so the message body is always a string Anthropic accepts.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content) if content is not None else ""


def _coerce_output_text(output: Any) -> str:
    """Coerce a ``function_call_output`` payload to tool_result text.

    The executor stores the tool result as a JSON string; we pass it
    through unchanged when it's a string and serialise dicts/lists so
    the model always sees a textual tool result.
    """
    if isinstance(output, str):
        return output
    if output is None:
        return ""
    try:
        return json.dumps(output)
    except (TypeError, ValueError):
        return str(output)


def _extract_output_text(raw: dict[str, Any]) -> str:
    """Concatenate every ``text`` block from the Messages ``content``.

    Returns the newline-joined text of all ``{"type": "text", ...}``
    blocks. When the response carries no text block (e.g. a pure
    tool_use turn) we fall back to a pretty-printed dump so the audit
    layer never silently drops the turn.
    """
    chunks: list[str] = []
    for block in raw.get("content", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            chunks.append(block["text"])
    if chunks:
        return "\n".join(chunks).strip()
    # A tool_use-only turn has no text; don't dump the raw body in that
    # case (the tool calls carry the signal). Only dump when there is
    # neither text nor any tool_use block.
    has_tool_use = any(
        isinstance(block, dict) and block.get("type") == "tool_use"
        for block in raw.get("content", [])
    )
    if has_tool_use:
        return ""
    return json.dumps(raw, indent=2)


def _extract_tool_calls(raw: dict[str, Any]) -> list[ProviderToolCall]:
    """Adapt every ``tool_use`` block to a :class:`ProviderToolCall`.

    Field rename: Anthropic ``{"id", "name", "input"}`` →
    aiswmm ``ProviderToolCall(call_id, name, arguments)``. Blocks
    without a string ``name`` are skipped.
    """
    calls: list[ProviderToolCall] = []
    for block in raw.get("content", []):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if not isinstance(name, str):
            continue
        arguments = block.get("input")
        if not isinstance(arguments, dict):
            arguments = {}
        call_id = block.get("id") or f"call_{len(calls) + 1}"
        calls.append(ProviderToolCall(call_id=str(call_id), name=name, arguments=arguments))
    return calls


def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_mock_tool_calls(text: str) -> list[ProviderToolCall]:
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise RuntimeError("AISWMM_ANTHROPIC_MOCK_TOOL_CALLS must be a JSON list.")
    calls: list[ProviderToolCall] = []
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise RuntimeError("AISWMM_ANTHROPIC_MOCK_TOOL_CALLS items must be objects.")
        name = item.get("name")
        if not isinstance(name, str):
            raise RuntimeError("AISWMM_ANTHROPIC_MOCK_TOOL_CALLS items need a string name.")
        arguments = item.get("arguments", {})
        if not isinstance(arguments, dict):
            raise RuntimeError("AISWMM_ANTHROPIC_MOCK_TOOL_CALLS arguments must be objects.")
        calls.append(ProviderToolCall(call_id=str(item.get("call_id", f"mock-call-{index}")), name=name, arguments=arguments))
    return calls


def _mock_call_payload(call: ProviderToolCall) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": call.call_id,
        "name": call.name,
        "input": call.arguments,
    }


__all__ = ["AnthropicProvider"]
