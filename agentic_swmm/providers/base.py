from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderResult:
    text: str
    model: str
    raw: dict


@dataclass(frozen=True)
class ProviderToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ProviderToolResponse:
    text: str
    model: str
    response_id: str | None
    tool_calls: list[ProviderToolCall]
    raw: dict[str, Any]


class ChatProvider(Protocol):
    def complete(self, *, system_prompt: str, prompt: str) -> ProviderResult:
        ...
