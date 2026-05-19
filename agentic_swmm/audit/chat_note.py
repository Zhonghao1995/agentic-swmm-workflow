"""Chat-note generator.

Builds Obsidian-compatible ``chat_note.md`` content for an interactive
chat-only session. Pure function over already-parsed dicts; the caller
writes the returned string to ``<session-dir>/chat_note.md``.

PRD: ``.claude/prds/PRD_audit.md`` ("Module: ChatNote generator").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


_DEFAULT_TAGS = ("agentic-swmm", "chat-session")


@dataclass(frozen=True)
class MemoryInformedDecision:
    """One row in the chat-note "Memory-informed defaults" table.

    Frozen because the renderer formats decisions into Markdown and
    should not be tempted to mutate the input.

    ``field`` is the decision label (e.g. ``"plot_node"``,
    ``"rain_kind"``). ``value`` is the value the agent chose
    (rendered with ``str()``). ``source`` is a short human-readable
    explanation of where the choice came from — typically a phrase
    like ``"5 of 5 prior Tecnopolo runs"`` or
    ``"memory consensus across 3 hits"``.
    """

    field: str
    value: Any
    source: str


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


def _escape_md_cell(value: Any) -> str:
    """Escape pipe characters so a value never breaks the Markdown table.

    GitHub-flavoured Markdown uses ``|`` as the column separator; a
    raw pipe in a cell silently splits the row. We escape with a
    backslash, which Obsidian and most renderers honour.
    """
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def render_memory_informed_defaults_section(
    decisions: list[MemoryInformedDecision],
) -> str:
    """Return a Markdown section describing memory-informed defaults.

    Returns an empty string when ``decisions`` is empty so the caller
    can splice the result into a larger document without an explicit
    branch. A non-empty section always carries the standard heading
    plus a three-column table (Field / Value / Source) and a trailing
    blank line.

    The table is intentionally minimal — three columns rather than
    six — because the goal is to summarise what the agent picked
    *because of memory*, not to reproduce the full memory_trace.
    Readers who want the audit-grade detail open
    ``memory_trace.jsonl``.
    """
    if not decisions:
        return ""
    lines: list[str] = [
        "## Memory-informed defaults",
        "",
        "| Field | Value | Source |",
        "|---|---|---|",
    ]
    for decision in decisions:
        field = _escape_md_cell(decision.field)
        value = _escape_md_cell(decision.value)
        source = _escape_md_cell(decision.source)
        lines.append(f"| {field} | {value} | {source} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def _extract_memory_informed_decisions(
    agent_trace: list[dict],
) -> list[MemoryInformedDecision]:
    """Walk ``agent_trace`` for ``memory_informed_decision`` events.

    The runtime mirrors each memory-informed decision into the
    agent-level trace (PRD-07 §2) as well as the per-run
    ``memory_trace.jsonl``. The chat-note generator reads only the
    agent_trace events so the renderer stays I/O-free — the loader
    that produced ``agent_trace`` already parsed the file.

    Events without a ``field`` are skipped: the renderer must produce
    a complete row or no row at all. The order in ``agent_trace`` is
    preserved so readers see decisions in the order the agent made
    them.
    """
    out: list[MemoryInformedDecision] = []
    for event in agent_trace:
        if not isinstance(event, dict):
            continue
        if event.get("event") != "memory_informed_decision":
            continue
        field = event.get("field")
        if not isinstance(field, str) or not field.strip():
            continue
        value = event.get("value_chosen")
        rationale = event.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            # Fall back to source_runs count when the writer omitted a
            # rationale string. The chat note still has to print a
            # non-empty Source cell.
            sources = event.get("source_runs")
            if isinstance(sources, list) and sources:
                rationale = f"{len(sources)} prior run(s)"
            else:
                rationale = "memory consultation"
        out.append(
            MemoryInformedDecision(
                field=field.strip(),
                value=value,
                source=rationale.strip(),
            )
        )
    return out


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

    # PRD-07 §2: insert the Memory-informed defaults section between
    # "What user asked" and "What agent did" so the reader sees the
    # *defaults the agent inherited from memory* before they read the
    # tool call sequence those defaults shaped.
    decisions = _extract_memory_informed_decisions(agent_trace)
    section = render_memory_informed_defaults_section(decisions)
    if section:
        # ``section`` already ends with a single trailing newline; we
        # split on newlines so it joins cleanly with the surrounding
        # ``"\n".join(lines)`` at the bottom of this function.
        for chunk in section.rstrip("\n").splitlines():
            lines.append(chunk)
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
