"""Memory-recall and fact-recording handlers (PRD #128).

Family: agent-internal memory (the store under ``memory/store``).

The three memory tools share token-budget helpers. They are grouped
here because:

- they all sit behind ``<memory-context>`` fences (audit / prompt-injection
  defence),
- they all read or write the one store (ledgers, their tables, the
  session database),
- they share token-budget bookkeeping.

``_failure`` comes from ``tool_handlers/_shared`` — the cross-cutting
helpers every family imports.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import resolve_memory_dir


_RECALL_TOKEN_BUDGET = 1000
_RECALL_SESSION_HISTORY_TOKEN_BUDGET = 1000


def _estimated_tokens(text: str) -> int:
    """Cheap word/4 token estimator; PRD uses this same heuristic."""
    return max(1, len(text.split())) if text else 0


def _truncate_to_token_budget(text: str, budget: int) -> str:
    if not text:
        return text
    if _estimated_tokens(text) <= budget:
        return text
    words = text.split()
    return " ".join(words[: max(1, budget)]) + "\n...[truncated]"


def _recall_memory_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Keyword recall over the store's failures and negative lessons.

    Memory simplification PR 3b: the pattern lookup in a generated
    ``lessons_learned.md`` and the RAG search are gone; this reads the
    two ledgers through their tables (``memory.recall``). The payload is
    wrapped in a ``<memory-context>`` fence (source ``"store"``).
    """
    from agentic_swmm.memory import recall_memory as _recall
    from agentic_swmm.memory.context_fence import wrap as _wrap_fence

    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    case_name = call.args.get("case_name")
    case_name = str(case_name).strip() if isinstance(case_name, str) and case_name.strip() else None
    limit = int(call.args.get("limit") or 5)

    try:
        hits = _recall(query, resolve_memory_dir(), case_name=case_name, limit=limit)
    except Exception as exc:
        return _failure(call, f"recall_memory failed: {exc}")

    if not hits:
        wrapped = _wrap_fence("", source="store", stale=False)
        return {
            "tool": call.name,
            "args": call.args,
            "ok": True,
            "results": [],
            "excerpt": wrapped,
            "chars": len(wrapped),
            "summary": f"recall_memory: no match for '{query}'",
        }

    rendered = json.dumps(hits, ensure_ascii=False, indent=2)
    truncated = _truncate_to_token_budget(rendered, _RECALL_TOKEN_BUDGET)
    wrapped = _wrap_fence(truncated, source="store", stale=False)
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": hits,
        "excerpt": wrapped,
        "chars": len(wrapped),
        "summary": f"recall_memory: {len(hits)} hit(s) for '{query}' ({_estimated_tokens(truncated)} est. tokens)",
    }


def _recall_session_history_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Search prior chat sessions in the cross-session SQLite store.

    The handler returns its payload wrapped in a ``<memory-context>``
    fence (source ``"sessions"``) so the planner's prompt-injection
    defences extend automatically to this new layer.
    """
    from agentic_swmm.memory import session_db as _session_db
    from agentic_swmm.memory.context_fence import wrap as _wrap_fence
    from agentic_swmm.memory.session_sync import default_db_path

    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    case_name = call.args.get("case_name")
    case_name = str(case_name).strip() if isinstance(case_name, str) and case_name.strip() else None
    limit = int(call.args.get("limit") or 5)

    db_path = default_db_path()
    if not db_path.exists():
        wrapped = _wrap_fence("(no prior sessions recorded)", source="sessions", stale=False)
        return {
            "tool": call.name,
            "args": call.args,
            "ok": True,
            "results": [],
            "excerpt": wrapped,
            "chars": len(wrapped),
            "summary": "recall_session_history: store not initialised yet",
        }

    try:
        with _session_db.connect(db_path) as conn:
            hits = _session_db.search_messages(
                conn, query, case_name=case_name, limit=limit
            )
    except Exception as exc:
        return _failure(call, f"recall_session_history failed: {exc}")

    rendered = json.dumps(hits, ensure_ascii=False, indent=2)
    truncated = _truncate_to_token_budget(rendered, _RECALL_SESSION_HISTORY_TOKEN_BUDGET)
    wrapped = _wrap_fence(truncated, source="sessions", stale=False)
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": hits,
        "excerpt": wrapped,
        "chars": len(wrapped),
        "summary": f"recall_session_history: {len(hits)} session(s) matched query",
    }


def _record_fact_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Append a candidate fact block to ``facts_staging.md``.

    This is the only write path the LLM has into the facts layer; the
    user promotes from staging into ``facts.md`` manually via the
    ``aiswmm memory promote-facts`` CLI. Marking the tool ``is_read_only=False``
    keeps it out of ``Profile.QUICK`` auto-approve.
    """
    from agentic_swmm.memory import append_fact

    text = str(call.args.get("text") or "").strip()
    if not text:
        return _failure(call, "text is required")
    source_id = call.args.get("source_session_id")
    source_id = str(source_id).strip() if isinstance(source_id, str) and source_id.strip() else None
    try:
        staging_path = append_fact(text, source_session_id=source_id)
    except Exception as exc:
        return _failure(call, f"record_fact failed: {exc}")
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "path": str(staging_path),
        "summary": "fact appended to staging; run `aiswmm memory promote-facts` to review",
    }


__all__ = [
    "_recall_memory_tool",
    "_recall_session_history_tool",
    "_record_fact_tool",
    "tool_specs",
]


def tool_specs():
    """This family's planner tools (issue #358 self-registration)."""
    from agentic_swmm.agent.tool_handlers._shared import _object
    from agentic_swmm.agent.types import ToolSpec

    return [
        ToolSpec(
            "recall_memory",
            (
                "Recall what this project's memory store holds about a question: "
                "tool calls that failed before, with the fix that worked when one was "
                "recorded, and parameter regions known to be bad (negative lessons). "
                "Returns up to `limit` rows with kind, run_id, case_name, score and text.\n"
                "USE WHEN: the user asks what went wrong before, what was learned, or "
                "whether a failure or parameter set was seen before.\n"
                "DO NOT USE WHEN: the question is about a previous chat conversation "
                "(prefer recall_session_history) or unrelated to past runs."
            ),
            _object(
                {
                    "query": {"type": "string"},
                    "case_name": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                ["query"],
            ),
            _recall_memory_tool,
            is_read_only=True,
        ),
        ToolSpec(
            "recall_session_history",
            (
                "Search prior chat sessions in the SQLite session store for relevant past work.\n"
                "USE WHEN: user mentions '上次/昨天/上周/before/previously/continue', or you need "
                "to check whether a similar question / failure pattern has been encountered before.\n"
                "DO NOT USE WHEN: question has no temporal cue and current-session context is sufficient."
            ),
            _object(
                {
                    "query": {"type": "string"},
                    "case_name": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                ["query"],
            ),
            _recall_session_history_tool,
            is_read_only=True,
        ),
        ToolSpec(
            "record_fact",
            (
                "Append a candidate project fact to the staging file for later user review.\n"
                "USE WHEN: user just expressed a durable preference, project convention, or "
                "confirmed fix recipe that future sessions should remember.\n"
                "DO NOT USE WHEN: ephemeral state, file path, secret, or anything you are not "
                "certain the user wants persisted."
            ),
            _object(
                {"text": {"type": "string"}, "source_session_id": {"type": "string"}},
                ["text"],
            ),
            _record_fact_tool,
            is_read_only=False,
        ),
    ]
