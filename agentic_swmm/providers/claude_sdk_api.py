"""Claude Agent SDK provider (PRD-09).

This module wraps the Anthropic-published ``claude-agent-sdk`` package
so the aiswmm planner / runtime / tool-registry surfaces can talk to
Claude through the user's local Claude Code installation. When a user
has logged in via ``claude login`` the SDK walks their Pro / Max
**subscription** quota — no second API key required. When the user
prefers an API token they can fall back to ``ANTHROPIC_API_KEY`` and
the SDK picks that up automatically; we do not parse credentials.

Auth resolution is delegated to the SDK and the underlying ``claude``
CLI in this order:

1. Locally-stored OAuth credentials (macOS Keychain
   "Claude Code-credentials" generic password, or
   ``~/.claude/.credentials.json`` on Linux). This is the subscription
   quota path.
2. ``ANTHROPIC_API_KEY`` environment variable (raw API path).

The provider mirrors :class:`agentic_swmm.providers.openai_api.OpenAIProvider`'s
public surface so the factory can swap one for the other.

### Async-to-sync bridge

``claude_agent_sdk.query()`` is an async generator. The aiswmm planner
runs in a synchronous main loop. We invoke the SDK inside a fresh
event loop owned by a daemon worker thread; this side-steps the
"loop is already running" RuntimeError that bites ``asyncio.run()``
when the caller is itself an asyncio task (e.g. inside a
``pytest-asyncio`` fixture).

### Tool-schema mapping

aiswmm tool descriptors are flat function-call schemas
(``{"type": "function", "name": ..., "parameters": {...}}``). The SDK
exposes ``ClaudeAgentOptions.allowed_tools`` (list of names) plus an
MCP-shaped server config. Since the planner already advertises tool
schemas inside the system prompt, we forward the tool **names** to
``allowed_tools`` and rely on the system prompt for schema details.

### Multi-turn

The OpenAI Responses API tracks turn history server-side via
``previous_response_id``; the SDK takes the full message list each
call. The ``previous_response_id`` argument is accepted (Protocol
parity) and ignored. The provider reconstructs the dialogue from
``input_items`` and concatenates it into a single prompt string per
SDK call.
"""
from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from agentic_swmm.providers.base import (
    ProviderResult,
    ProviderToolCall,
    ProviderToolResponse,
)


class ClaudeSDKProvider:
    """Routes aiswmm's planner-facing provider Protocol through
    ``claude-agent-sdk``.

    The SDK is imported lazily inside :meth:`_load_sdk` so that users
    who never install the optional extra never pay the import cost.

    Parameters
    ----------
    model:
        The Anthropic model name (e.g. ``claude-sonnet-4-5-20250929``).
        Forwarded to ``ClaudeAgentOptions.model``.
    timeout:
        Wall-clock seconds before the daemon worker thread is
        abandoned. Defaults to 120s to match the OpenAI provider's
        ``urlopen`` timeout.
    """

    def __init__(self, *, model: str | None = None, timeout: int = 120) -> None:
        self.model = model
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(self, *, system_prompt: str, prompt: str) -> ProviderResult:
        """One-shot text completion. Mirrors
        :meth:`OpenAIProvider.complete`.

        ``system_prompt`` becomes ``ClaudeAgentOptions.system_prompt``;
        ``prompt`` is passed directly as the SDK ``query()`` prompt.
        """
        sdk = self._load_sdk()
        options = sdk.ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=self.model,
        )
        messages = self._run_sync(sdk.query, prompt=prompt, options=options)
        text = _collect_text(messages, sdk)
        raw = {"messages": _summarize_messages(messages, sdk)}
        return ProviderResult(text=text, model=self.model or "", raw=raw)

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        """Multi-turn variant that surfaces ``ToolUseBlock`` calls.

        ``previous_response_id`` is accepted for Protocol parity with
        :class:`OpenAIProvider` and ignored — the SDK keeps no
        server-side response state, so the caller must pass the full
        ``input_items`` history each turn.
        """
        sdk = self._load_sdk()
        allowed = _allowed_tool_names(tools)
        prompt = _flatten_input_items(input_items)
        options = sdk.ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=self.model,
            allowed_tools=allowed,
        )
        messages = self._run_sync(sdk.query, prompt=prompt, options=options)
        text = _collect_text(messages, sdk)
        tool_calls = _collect_tool_calls(messages, sdk)
        response_id = _extract_session_id(messages, sdk)
        raw = {
            "messages": _summarize_messages(messages, sdk),
            "allowed_tools": allowed,
        }
        return ProviderToolResponse(
            text=text,
            model=self.model or "",
            response_id=response_id,
            tool_calls=tool_calls,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _load_sdk():
        """Lazy import wrapper that re-raises SDK absence as
        ``ImportError`` so the factory can catch it and surface the
        pip-extra install command."""
        try:
            import claude_agent_sdk  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - factory test exercises this
            raise ImportError(
                "claude_agent_sdk is required for the claude_sdk provider"
            ) from exc
        return claude_agent_sdk

    def _run_sync(self, query_fn, *, prompt: str, options) -> list[Any]:
        """Drive the SDK's async generator from sync code.

        We always spin up a fresh event loop in a daemon worker thread
        so we never collide with an outer asyncio runtime (e.g. a
        ``pytest-asyncio`` fixture). The thread is joined with the
        provider's ``timeout`` to bound the planner's per-turn wall
        clock.

        Errors raised inside the worker are stashed and re-raised on
        the main thread so they map cleanly through :func:`_wrap_error`.
        """
        result: dict[str, Any] = {"messages": [], "error": None}

        def _worker() -> None:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                result["messages"] = loop.run_until_complete(
                    _drain(query_fn, prompt=prompt, options=options)
                )
            except BaseException as exc:  # noqa: BLE001 - re-raise on main thread
                result["error"] = exc
            finally:
                try:
                    loop.close()
                except Exception:  # pragma: no cover - defensive
                    pass

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(timeout=self.timeout)
        if thread.is_alive():
            raise RuntimeError(
                f"Claude Agent SDK call did not complete within {self.timeout}s. "
                "If this is a long-running query, increase the provider timeout."
            )
        if result["error"] is not None:
            raise _wrap_error(result["error"], self._load_sdk())
        return result["messages"]


async def _drain(query_fn, *, prompt: str, options) -> list[Any]:
    """Consume an async iterator into a list. Tiny helper kept at
    module scope so it's easy to monkeypatch from tests."""
    out = []
    async for msg in query_fn(prompt=prompt, options=options):
        out.append(msg)
    return out


# ---------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------


def _allowed_tool_names(tools: list[dict[str, Any]]) -> list[str]:
    """Extract function-call tool names from aiswmm's tool descriptor list.

    The OpenAI Responses API tool shape is
    ``{"type": "function", "name": ..., "parameters": ...}``. The SDK
    only needs the name string for its allow-list. Tools without a
    string ``name`` are skipped silently rather than raising — keeps
    the provider resilient to upstream schema drift.
    """
    names: list[str] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _flatten_input_items(input_items: list[dict[str, Any]]) -> str:
    """Collapse a multi-turn ``input_items`` history into one prompt.

    The OpenAI provider passes ``input_items`` as a list of
    ``{"role": "user"|"assistant"|"system", "content": ...}`` dicts
    plus function-call/function-output items between turns. The SDK
    only consumes a single prompt string per ``query()`` call, so we
    serialise each item as ``[role] content`` and join with blank
    lines. Function-call / function-output items become tagged blocks
    so the model sees the prior tool exchange.
    """
    if not input_items:
        return ""
    lines: list[str] = []
    for item in input_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "function_call":
            name = item.get("name", "")
            args = item.get("arguments", "")
            lines.append(f"[assistant tool-call] {name}({args})")
            continue
        if item_type == "function_call_output":
            output = item.get("output", "")
            lines.append(f"[tool-output] {output}")
            continue
        role = item.get("role", "user")
        content = item.get("content", "")
        if isinstance(content, list):
            # Responses-API rich-content blocks: pull text out.
            parts = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            content = "\n".join(parts)
        lines.append(f"[{role}] {content}".rstrip())
    return "\n\n".join(lines)


def _collect_text(messages: list[Any], sdk) -> str:
    """Concatenate every ``TextBlock`` from every ``AssistantMessage``.

    Thinking blocks are deliberately ignored to mirror the OpenAI
    provider's text-only contract; surfacing them is a future PRD.
    """
    chunks: list[str] = []
    AssistantMessage = sdk.AssistantMessage
    TextBlock = sdk.TextBlock
    for msg in messages:
        if not isinstance(msg, AssistantMessage):
            continue
        for block in getattr(msg, "content", []) or []:
            if isinstance(block, TextBlock):
                chunks.append(block.text)
    return "\n".join(chunks).strip()


def _collect_tool_calls(messages: list[Any], sdk) -> list[ProviderToolCall]:
    """Walk every ``AssistantMessage`` and adapt ``ToolUseBlock``
    instances to aiswmm's :class:`ProviderToolCall`.

    Field rename: SDK ``ToolUseBlock(id, name, input)`` →
    aiswmm ``ProviderToolCall(call_id, name, arguments)``.
    """
    calls: list[ProviderToolCall] = []
    AssistantMessage = sdk.AssistantMessage
    ToolUseBlock = sdk.ToolUseBlock
    for msg in messages:
        if not isinstance(msg, AssistantMessage):
            continue
        for block in getattr(msg, "content", []) or []:
            if isinstance(block, ToolUseBlock):
                args = block.input if isinstance(block.input, dict) else {}
                calls.append(
                    ProviderToolCall(
                        call_id=str(block.id),
                        name=str(block.name),
                        arguments=args,
                    )
                )
    return calls


def _extract_session_id(messages: list[Any], sdk) -> str | None:
    """Use the SDK's ``ResultMessage.session_id`` as our ``response_id``.

    aiswmm's OpenAI path stores the Responses-API response id here so
    the planner can chain turns; the SDK's analogue is the session id.
    Returns the first non-empty session_id we find.
    """
    ResultMessage = sdk.ResultMessage
    AssistantMessage = sdk.AssistantMessage
    for msg in messages:
        sid = None
        if isinstance(msg, ResultMessage):
            sid = getattr(msg, "session_id", None)
        elif isinstance(msg, AssistantMessage):
            sid = getattr(msg, "session_id", None)
        if isinstance(sid, str) and sid:
            return sid
    return None


def _summarize_messages(messages: list[Any], sdk) -> list[dict[str, Any]]:
    """Best-effort serialisation of the message stream into ``raw``.

    We capture enough structure for the audit layer to inspect the
    turn without serialising the full SDK objects (some carry
    non-serialisable callables).
    """
    out: list[dict[str, Any]] = []
    AssistantMessage = sdk.AssistantMessage
    TextBlock = sdk.TextBlock
    ToolUseBlock = sdk.ToolUseBlock
    ResultMessage = sdk.ResultMessage
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            blocks: list[dict[str, Any]] = []
            for block in getattr(msg, "content", []) or []:
                if isinstance(block, TextBlock):
                    blocks.append({"type": "text", "text": block.text})
                elif isinstance(block, ToolUseBlock):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                else:
                    blocks.append({"type": type(block).__name__})
            out.append(
                {
                    "type": "assistant",
                    "content": blocks,
                    "model": getattr(msg, "model", None),
                    "session_id": getattr(msg, "session_id", None),
                }
            )
        elif isinstance(msg, ResultMessage):
            out.append(
                {
                    "type": "result",
                    "subtype": getattr(msg, "subtype", None),
                    "is_error": getattr(msg, "is_error", None),
                    "total_cost_usd": getattr(msg, "total_cost_usd", None),
                    "session_id": getattr(msg, "session_id", None),
                }
            )
        else:
            out.append({"type": type(msg).__name__})
    return out


def _wrap_error(exc: BaseException, sdk) -> BaseException:
    """Map SDK exceptions to ``RuntimeError`` with actionable hints.

    Order matters: ``CLINotFoundError`` is a subclass of
    ``CLIConnectionError`` so check it first.
    """
    CLINotFoundError = getattr(sdk, "CLINotFoundError", None)
    CLIConnectionError = getattr(sdk, "CLIConnectionError", None)
    ProcessError = getattr(sdk, "ProcessError", None)
    CLIJSONDecodeError = getattr(sdk, "CLIJSONDecodeError", None)
    ClaudeSDKError = getattr(sdk, "ClaudeSDKError", None)

    if CLINotFoundError and isinstance(exc, CLINotFoundError):
        return RuntimeError(
            "Claude Agent SDK could not find the `claude` CLI. "
            "Install it from https://docs.claude.com/en/docs/claude-code "
            "or set CLAUDE_CODE_NPM_PREFIX, then re-run."
        )
    if CLIConnectionError and isinstance(exc, CLIConnectionError):
        return RuntimeError(
            "Claude Agent SDK could not connect to the `claude` CLI. "
            "Verify your subscription session with `claude login`."
        )
    if ProcessError and isinstance(exc, ProcessError):
        stderr = getattr(exc, "stderr", "") or ""
        suffix = f" Stderr: {stderr.strip()}" if stderr else ""
        return RuntimeError(
            f"Claude Agent SDK subprocess failed.{suffix}"
        )
    if CLIJSONDecodeError and isinstance(exc, CLIJSONDecodeError):
        return RuntimeError(
            "Claude Agent SDK transport corruption (JSON decode error). "
            "Re-run the request; if it persists upgrade the `claude` CLI."
        )
    if ClaudeSDKError and isinstance(exc, ClaudeSDKError):
        return RuntimeError(f"Claude Agent SDK error: {exc}")
    return exc


__all__ = ["ClaudeSDKProvider"]
