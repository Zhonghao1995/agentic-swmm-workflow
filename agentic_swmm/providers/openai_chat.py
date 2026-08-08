"""OpenAI chat/completions wire client — the third-party lingua franca.

``OpenAIChatProvider`` speaks ``POST <base_url>/chat/completions`` with
standard function calling. It exists because most OpenAI-*compatible*
endpoints (OpenRouter, DeepSeek, Groq, Gemini's compatibility layer,
Ollama, LM Studio, vLLM, corporate proxies) implement chat/completions
and not the Responses API that :mod:`openai_api` speaks.

Protocol adaptation mirrors :mod:`anthropic_api`: chat/completions keeps
no server-side state, and every ``tool`` role message must follow the
assistant message carrying its ``tool_calls``. The planner sends only
the per-turn delta (correct for the stateful Responses provider), so we
keep the full message history locally: reset when
``previous_response_id`` is ``None``, append the translated delta, send
everything, then record the assistant turn we got back.

Mock seam mirrors the other two providers: ``AISWMM_CHAT_MOCK_RESPONSE``
short-circuits with a fixed text; ``AISWMM_CHAT_MOCK_TOOL_CALLS`` (a
JSON list) makes the first ``respond_with_tools`` call return those
calls, then falls through to the response mock. Keyless routes (Ollama,
LM Studio) construct with ``api_key=None`` and simply omit the
``Authorization`` header.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from agentic_swmm.providers._http import MissingCredentialsError, post_json_with_retry
from agentic_swmm.providers.base import ProviderResult, ProviderToolCall, ProviderToolResponse


class OpenAIChatProvider:
    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None = None,
        keyless: bool = False,
        label: str = "OpenAI-compatible",
        missing_key_error: str | None = None,
        auth_hint: str | None = None,
        timeout: int = 120,
    ) -> None:
        self.model = model
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.keyless = keyless
        self.label = label
        self.missing_key_error = missing_key_error or (
            f"No API key resolved for the {label} route. "
            "Run `aiswmm login` to store one."
        )
        self.auth_hint = auth_hint
        self.timeout = timeout
        self._mock_tool_calls_consumed = False
        self._history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # ChatProvider protocol
    # ------------------------------------------------------------------

    def complete(self, *, system_prompt: str, prompt: str) -> ProviderResult:
        mock_response = os.environ.get("AISWMM_CHAT_MOCK_RESPONSE")
        if mock_response is not None:
            return ProviderResult(text=mock_response, model=self.model, raw={"mock": True})
        self._require_ready()
        raw = self._post(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            }
        )
        return ProviderResult(text=_extract_message_text(raw), model=self.model, raw=raw)

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        mock_tool_calls = os.environ.get("AISWMM_CHAT_MOCK_TOOL_CALLS")
        if mock_tool_calls is not None and not self._mock_tool_calls_consumed:
            self._mock_tool_calls_consumed = True
            calls = _parse_mock_tool_calls(mock_tool_calls)
            raw = {"mock": True, "choices": [{"message": {"tool_calls": [_mock_call_payload(c) for c in calls]}}]}
            return ProviderToolResponse(
                text="", model=self.model, response_id="mock-response-1", tool_calls=calls, raw=raw
            )
        mock_response = os.environ.get("AISWMM_CHAT_MOCK_RESPONSE")
        if mock_response is not None:
            return ProviderToolResponse(
                text=mock_response,
                model=self.model,
                response_id="mock-response-final",
                tool_calls=[],
                raw={"mock": True, "output_text": mock_response},
            )
        self._require_ready()

        if previous_response_id is None:
            self._history = []
        self._history.extend(_translate_input_items(input_items))
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(self._history)
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        translated_tools = _translate_tools(tools)
        if translated_tools:
            payload["tools"] = translated_tools
        raw = self._post(payload)

        assistant_message = _first_choice_message(raw)
        if assistant_message:
            # Record the assistant turn verbatim so next turn's ``tool``
            # role messages land after their ``tool_calls`` anchor.
            self._history.append(assistant_message)
        return ProviderToolResponse(
            text=_extract_message_text(raw),
            model=self.model,
            # chat/completions is stateless; the id is echoed for audit
            # only and doubles as the "history continues" marker the
            # planner passes back as ``previous_response_id``.
            response_id=raw.get("id") if isinstance(raw.get("id"), str) else "chat-history",
            tool_calls=_extract_tool_calls(raw),
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_ready(self) -> None:
        if not self.base_url:
            raise RuntimeError(
                f"No base URL configured for the {self.label} route. "
                "Set it in ~/.aiswmm/config.toml ([<route>] base_url) or the "
                "route's AISWMM_<ROUTE>_BASE_URL environment variable."
            )
        if not self.api_key and not self.keyless:
            raise MissingCredentialsError(self.missing_key_error)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            method="POST",
            headers=headers,
        )
        return post_json_with_retry(
            request, timeout=self.timeout, provider_label=self.label, auth_hint=self.auth_hint
        )


# ----------------------------------------------------------------------
# Translation helpers (Responses-style items <-> chat messages)
# ----------------------------------------------------------------------


def _translate_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map aiswmm's flat tool descriptors to chat/completions shape.

    aiswmm tools are Responses-style ``{"type": "function", "name",
    "parameters", "description"}``; chat/completions nests them as
    ``{"type": "function", "function": {...}}``. Descriptors without a
    string ``name`` are skipped rather than raising.
    """
    translated: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        parameters = tool.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        translated.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description") or "",
                    "parameters": parameters,
                },
            }
        )
    return translated


def _translate_input_items(input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map OpenAI-Responses ``input_items`` onto chat messages.

    * ``function_call`` → assistant message with a ``tool_calls`` entry
      (arguments re-serialised to the JSON string the wire expects);
    * ``function_call_output`` → ``tool`` role message keyed by
      ``tool_call_id``;
    * anything else → plain ``{"role", "content"}`` message with rich
      content flattened to text. Non-dict items are skipped.
    """
    messages: list[dict[str, Any]] = []
    for item in input_items or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "")
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": str(item.get("name") or ""),
                                "arguments": json.dumps(
                                    _parse_arguments(item.get("arguments", "{}")), sort_keys=True
                                ),
                            },
                        }
                    ],
                }
            )
            continue
        if item_type == "function_call_output":
            call_id = str(item.get("call_id") or item.get("id") or "")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _coerce_output_text(item.get("output", "")),
                }
            )
            continue
        role = item.get("role", "user")
        if role not in ("user", "assistant", "system"):
            role = "user"
        messages.append({"role": role, "content": _coerce_content_text(item.get("content", ""))})
    return messages


def _coerce_content_text(content: Any) -> str:
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
    if isinstance(output, str):
        return output
    if output is None:
        return ""
    try:
        return json.dumps(output)
    except (TypeError, ValueError):
        return str(output)


# ----------------------------------------------------------------------
# Response parsing
# ----------------------------------------------------------------------


def _first_choice_message(raw: dict[str, Any]) -> dict[str, Any] | None:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    return message if isinstance(message, dict) else None


def _extract_message_text(raw: dict[str, Any]) -> str:
    message = _first_choice_message(raw)
    if message is None:
        return json.dumps(raw, indent=2)
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    # Rich-content responses (some compatible servers return block lists).
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        if parts:
            return "\n".join(parts).strip()
    # A pure tool_calls turn has no text; the calls carry the signal.
    if message.get("tool_calls"):
        return ""
    return json.dumps(raw, indent=2)


def _extract_tool_calls(raw: dict[str, Any]) -> list[ProviderToolCall]:
    message = _first_choice_message(raw)
    if message is None:
        return []
    calls: list[ProviderToolCall] = []
    for entry in message.get("tool_calls") or []:
        if not isinstance(entry, dict):
            continue
        function = entry.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = _parse_arguments(function.get("arguments", "{}"))
        call_id = entry.get("id") or f"call_{len(calls) + 1}"
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
        raise RuntimeError("AISWMM_CHAT_MOCK_TOOL_CALLS must be a JSON list.")
    calls: list[ProviderToolCall] = []
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise RuntimeError("AISWMM_CHAT_MOCK_TOOL_CALLS items must be objects.")
        name = item.get("name")
        if not isinstance(name, str):
            raise RuntimeError("AISWMM_CHAT_MOCK_TOOL_CALLS items need a string name.")
        arguments = item.get("arguments", {})
        if not isinstance(arguments, dict):
            raise RuntimeError("AISWMM_CHAT_MOCK_TOOL_CALLS arguments must be objects.")
        calls.append(
            ProviderToolCall(call_id=str(item.get("call_id", f"mock-call-{index}")), name=name, arguments=arguments)
        )
    return calls


def _mock_call_payload(call: ProviderToolCall) -> dict[str, Any]:
    return {
        "id": call.call_id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments, sort_keys=True)},
    }


__all__ = ["OpenAIChatProvider"]
