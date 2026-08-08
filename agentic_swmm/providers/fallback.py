"""Fallback chain: keep a session alive when the primary route dies.

``FallbackProvider`` wraps two :class:`~agentic_swmm.providers.base.ChatProvider`
instances behind the same protocol. It dispatches to the primary until the
primary fails *structurally* (missing credentials, connection-level failure
after retries, or HTTP 401/403/429/5xx), then switches to the fallback for the
rest of the process, warning once on stderr. Model/request errors (other 4xx)
never engage the fallback: they indicate a config or request bug the user must
see, not an outage a local model can paper over.

Mid-conversation switches replay full context: the wrapper accumulates every
input item it forwards plus a synthesized record of each assistant turn the
primary produced (text and tool calls in Responses-item form). On switch, the
fallback receives the whole accumulated conversation with
``previous_response_id=None`` so its own history starts coherent. The replay is
exact for the two OpenAI wire formats; an ``anthropic-messages`` fallback may
reject mixed text+tool turns (strict role alternation), so prefer a local
chat-wire route (ollama, lmstudio) as the fallback, which is also what the
setup wizard offers.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from agentic_swmm.providers._http import (
    MissingCredentialsError,
    ProviderConnectionError,
    ProviderHTTPError,
)
from agentic_swmm.providers.base import (
    ChatProvider,
    ProviderResult,
    ProviderToolResponse,
)

# HTTP statuses that mean "the route is unusable right now" rather than
# "this request is malformed": auth failures and quota/server errors.
_FALLBACK_STATUSES = frozenset({401, 403, 429})


def _should_fall_back(exc: Exception) -> bool:
    if isinstance(exc, (MissingCredentialsError, ProviderConnectionError)):
        return True
    if isinstance(exc, ProviderHTTPError):
        return exc.status in _FALLBACK_STATUSES or exc.status >= 500
    return False


class FallbackProvider:
    def __init__(
        self,
        *,
        primary: ChatProvider,
        fallback: ChatProvider,
        primary_name: str,
        fallback_name: str,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_name = primary_name
        self._fallback_name = fallback_name
        self._switched = False
        # Responses-style item log of the whole conversation, kept so a
        # mid-run switch can replay full context into the fallback.
        self._items: list[dict[str, Any]] = []
        # Surface the active model for audit callers that read .model.
        self.model = getattr(primary, "model", None)

    # ------------------------------------------------------------------
    # ChatProvider protocol
    # ------------------------------------------------------------------

    def complete(self, *, system_prompt: str, prompt: str) -> ProviderResult:
        if self._switched:
            return self._fallback.complete(system_prompt=system_prompt, prompt=prompt)
        try:
            return self._primary.complete(system_prompt=system_prompt, prompt=prompt)
        except Exception as exc:  # noqa: BLE001 - filtered by _should_fall_back
            if not _should_fall_back(exc):
                raise
            self._engage(exc)
            return self._fallback.complete(system_prompt=system_prompt, prompt=prompt)

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        if self._switched:
            return self._fallback.respond_with_tools(
                system_prompt=system_prompt,
                input_items=input_items,
                tools=tools,
                previous_response_id=previous_response_id,
            )
        self._items.extend(item for item in input_items or [] if isinstance(item, dict))
        try:
            response = self._primary.respond_with_tools(
                system_prompt=system_prompt,
                input_items=input_items,
                tools=tools,
                previous_response_id=previous_response_id,
            )
        except Exception as exc:  # noqa: BLE001 - filtered by _should_fall_back
            if not _should_fall_back(exc):
                raise
            self._engage(exc)
            return self._fallback.respond_with_tools(
                system_prompt=system_prompt,
                input_items=list(self._items),
                tools=tools,
                previous_response_id=None,
            )
        self._record_assistant_turn(response)
        return response

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _engage(self, exc: Exception) -> None:
        self._switched = True
        self.model = getattr(self._fallback, "model", self.model)
        reason = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        print(
            f"warning: provider route '{self._primary_name}' failed ({reason}); "
            f"falling back to '{self._fallback_name}' for the rest of this session. "
            f"Fix the primary route or change provider.default / provider.fallback.",
            file=sys.stderr,
        )

    def _record_assistant_turn(self, response: ProviderToolResponse) -> None:
        """Append the primary's assistant turn to the replay log.

        Stateful primaries (OpenAI Responses) never resend assistant
        turns in ``input_items``, so without this record a switch would
        hand the fallback tool outputs whose tool calls were never
        declared. Text first, then the calls, in Responses-item form
        both wire adapters translate.
        """
        if response.text:
            self._items.append({"role": "assistant", "content": response.text})
        for call in response.tool_calls:
            self._items.append(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, sort_keys=True),
                }
            )


__all__ = ["FallbackProvider"]
