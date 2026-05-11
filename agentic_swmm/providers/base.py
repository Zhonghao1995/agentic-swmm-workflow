from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderResult:
    text: str
    model: str
    raw: dict


class ChatProvider(Protocol):
    def complete(self, *, system_prompt: str, prompt: str) -> ProviderResult:
        ...
