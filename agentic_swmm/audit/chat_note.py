"""Chat-note generator.

Builds Obsidian-compatible ``chat_note.md`` content for an interactive
chat-only session. Pure function over already-parsed dicts; the caller
writes the returned string to ``<session-dir>/chat_note.md``.

PRD: ``.claude/prds/PRD_audit.md`` ("Module: ChatNote generator").
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


_DEFAULT_TAGS = ("agentic-swmm", "chat-session")


def _yaml_scalar(value: Any) -> str:
    """Render a value as a safe YAML scalar (single line, escaped quotes)."""
    if value is None:
        return '""'
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{text}"'


def _frontmatter(session_state: dict, agent_trace: list[dict]) -> list[str]:
    case_id = session_state.get("case_id") or session_state.get("session_id") or "chat"
    date_raw = (
        session_state.get("created_at")
        or session_state.get("date")
        or datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    # Trim to YYYY-MM-DD when an ISO timestamp is supplied, for cleaner
    # Obsidian dataview grouping.
    date_field = str(date_raw)[:10] if len(str(date_raw)) >= 10 else str(date_raw)
    goal = session_state.get("goal") or _first_user_prompt(agent_trace) or ""
    status = session_state.get("status") or _derive_status(agent_trace)
    lines = [
        "---",
        "type: chat-session",
        f"case: {case_id}",
        f"date: {date_field}",
        f"goal: {_yaml_scalar(goal)}",
        f"status: {status}",
        "tags:",
    ]
    for tag in _DEFAULT_TAGS:
        lines.append(f"  - {tag}")
    lines.append("---")
    lines.append("")
    return lines


def _first_user_prompt(agent_trace: list[dict]) -> str | None:
    for event in agent_trace:
        if event.get("event") == "user_prompt":
            text = event.get("text") or event.get("prompt")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _derive_status(agent_trace: list[dict]) -> str:
    for event in reversed(agent_trace):
        if event.get("event") == "session_end":
            return "ok" if event.get("ok") else "fail"
    return "unknown"


def _what_user_asked(agent_trace: list[dict]) -> list[str]:
    prompts = []
    for event in agent_trace:
        if event.get("event") == "user_prompt":
            text = event.get("text") or event.get("prompt")
            if isinstance(text, str) and text.strip():
                prompts.append(text.strip())
    return prompts


def _tool_sequence(agent_trace: list[dict]) -> list[dict]:
    """Pair tool_call events with their tool_result events (best effort)."""
    pairs: list[dict] = []
    pending: dict | None = None
    for event in agent_trace:
        kind = event.get("event")
        if kind == "tool_call":
            pending = {"tool": event.get("tool"), "args": event.get("args") or {}, "summary": None, "ok": None}
            pairs.append(pending)
        elif kind == "tool_result" and pending is not None:
            pending["summary"] = event.get("summary")
            pending["ok"] = event.get("ok")
            pending = None
    return pairs


def _final_text(agent_trace: list[dict]) -> str | None:
    for event in reversed(agent_trace):
        if event.get("event") == "assistant_final":
            text = event.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _collect_artifacts(agent_trace: list[dict]) -> list[str]:
    seen: list[str] = []
    for event in agent_trace:
        if event.get("event") != "tool_result":
            continue
        for key in ("path", "stdout_file", "stderr_file", "artifact"):
            value = event.get(key)
            if isinstance(value, str) and value and value not in seen:
                seen.append(value)
    return seen


def build_chat_note(session_state: dict, agent_trace: list[dict]) -> str:
    """Return Obsidian-ready Markdown for a chat-only session.

    Inputs are already-parsed dicts; no I/O is performed.
    """
    if session_state is None:
        session_state = {}
    if agent_trace is None:
        agent_trace = []

    lines: list[str] = []
    lines.extend(_frontmatter(session_state, agent_trace))

    title = (
        session_state.get("case_id")
        or session_state.get("session_id")
        or "Chat Session"
    )
    lines.extend([f"# Chat Session - {title}", ""])

    goal = session_state.get("goal") or _first_user_prompt(agent_trace) or "(no goal recorded)"
    lines.extend(["## Goal", "", str(goal), ""])

    prompts = _what_user_asked(agent_trace)
    lines.extend(["## What user asked", ""])
    if prompts:
        for prompt in prompts:
            lines.append(f"- {prompt}")
    else:
        lines.append("- (no user prompts recorded)")
    lines.append("")

    lines.extend(["## What agent did", ""])
    pairs = _tool_sequence(agent_trace)
    if pairs:
        for index, pair in enumerate(pairs, start=1):
            tool = pair.get("tool") or "(unknown)"
            summary = pair.get("summary") or ""
            ok = pair.get("ok")
            status_marker = "OK" if ok else ("FAIL" if ok is False else "...")
            line = f"{index}. `{tool}` - {status_marker}"
            if summary:
                line = f"{line} - {summary}"
            lines.append(line)
    else:
        lines.append("- (no tool calls recorded)")
    lines.append("")

    final = _final_text(agent_trace)
    status = session_state.get("status") or _derive_status(agent_trace)
    lines.extend(["## Outcome", "", f"Status: {status}", ""])
    if final:
        lines.extend(["Final answer:", "", final, ""])

    artifacts = _collect_artifacts(agent_trace)
    if artifacts:
        lines.extend(["## Artifacts", ""])
        for artifact in artifacts:
            lines.append(f"- `{artifact}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
