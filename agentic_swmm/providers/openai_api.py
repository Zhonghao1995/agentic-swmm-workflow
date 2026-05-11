from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from agentic_swmm.providers.base import ProviderResult


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


class OpenAIProvider:
    def __init__(self, *, model: str, api_key: str | None = None, timeout: int = 120) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.timeout = timeout

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
