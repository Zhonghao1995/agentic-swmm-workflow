from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from agentic_swmm.providers.base import ProviderResult, ProviderToolCall, ProviderToolResponse


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


class OpenAIProvider:
    def __init__(self, *, model: str, api_key: str | None = None, timeout: int = 120) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.timeout = timeout
        self._mock_tool_calls_consumed = False

    def complete(self, *, system_prompt: str, prompt: str) -> ProviderResult:
        mock_response = os.environ.get("AISWMM_OPENAI_MOCK_RESPONSE")
        if mock_response is not None:
            return ProviderResult(text=mock_response, model=self.model, raw={"mock": True})
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Run `aiswmm config set openai.model <model>` after setting your API key.")

        payload = {
            "model": self.model,
            "instructions": system_prompt,
            "input": prompt,
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI API request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI API request failed: {exc.reason}") from exc

        return ProviderResult(text=_extract_output_text(raw), model=self.model, raw=raw)

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        mock_tool_calls = os.environ.get("AISWMM_OPENAI_MOCK_TOOL_CALLS")
        if mock_tool_calls is not None and not self._mock_tool_calls_consumed:
            self._mock_tool_calls_consumed = True
            calls = _parse_mock_tool_calls(mock_tool_calls)
            raw = {"mock": True, "output": [_mock_call_payload(call) for call in calls]}
            return ProviderToolResponse(text="", model=self.model, response_id="mock-response-1", tool_calls=calls, raw=raw)
        mock_response = os.environ.get("AISWMM_OPENAI_MOCK_RESPONSE")
        if mock_response is not None:
            return ProviderToolResponse(
                text=mock_response,
                model=self.model,
                response_id="mock-response-final",
                tool_calls=[],
                raw={"mock": True, "output_text": mock_response},
            )
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Set it or use AISWMM_OPENAI_MOCK_TOOL_CALLS for local tests.")

        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": system_prompt,
            "input": input_items,
            "tools": tools,
        }
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI API request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI API request failed: {exc.reason}") from exc

        return ProviderToolResponse(
            text=_extract_output_text(raw),
            model=self.model,
            response_id=raw.get("id") if isinstance(raw.get("id"), str) else None,
            tool_calls=_extract_tool_calls(raw),
            raw=raw,
        )


def _extract_output_text(raw: dict[str, Any]) -> str:
    output_text = raw.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    chunks: list[str] = []
    for item in raw.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    if chunks:
        return "\n".join(chunks).strip()
    return json.dumps(raw, indent=2)


def _extract_tool_calls(raw: dict[str, Any]) -> list[ProviderToolCall]:
    calls: list[ProviderToolCall] = []
    for item in raw.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        raw_arguments = item.get("arguments", "{}")
        arguments = _parse_arguments(raw_arguments)
        call_id = item.get("call_id") or item.get("id") or f"call_{len(calls) + 1}"
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
        raise RuntimeError("AISWMM_OPENAI_MOCK_TOOL_CALLS must be a JSON list.")
    calls: list[ProviderToolCall] = []
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise RuntimeError("AISWMM_OPENAI_MOCK_TOOL_CALLS items must be objects.")
        name = item.get("name")
        if not isinstance(name, str):
            raise RuntimeError("AISWMM_OPENAI_MOCK_TOOL_CALLS items need a string name.")
        arguments = item.get("arguments", {})
        if not isinstance(arguments, dict):
            raise RuntimeError("AISWMM_OPENAI_MOCK_TOOL_CALLS arguments must be objects.")
        calls.append(ProviderToolCall(call_id=str(item.get("call_id", f"mock-call-{index}")), name=name, arguments=arguments))
    return calls


def _mock_call_payload(call: ProviderToolCall) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call.call_id,
        "name": call.name,
        "arguments": json.dumps(call.arguments, sort_keys=True),
    }
