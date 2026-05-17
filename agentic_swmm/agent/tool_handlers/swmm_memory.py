"""Memory-recall and fact-recording handlers (PRD #128).

Family: ``swmm-modeling-memory`` + ``swmm-rag-memory``.

The four memory tools share token-budget helpers and the lessons /
RAG-index path resolvers. They are grouped here because:

- they all sit behind ``<memory-context>`` fences (audit / prompt-injection
  defence),
- they all consume the same lessons / rag-memory / session-DB storage
  triad,
- they share token-budget bookkeeping.

``_failure`` is imported from ``tool_registry`` via a late import to
avoid a circular dependency until the cross-cutting helpers settle in
``tool_handlers/_shared.py`` (a follow-up cleanup PR).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root


_RECALL_PATTERN_TOKEN_BUDGET = 500
_RECALL_SEARCH_TOKEN_BUDGET = 1000
_RECALL_SESSION_HISTORY_TOKEN_BUDGET = 1000


def _failure(call: ToolCall, summary: str) -> dict[str, Any]:
    """Standard failure payload shape; mirrored from ``tool_registry``."""
    return {"tool": call.name, "args": call.args, "ok": False, "summary": summary}


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


def _lessons_path() -> Path:
    """Resolve the curated lessons file path.

    Reads from ``AISWMM_LESSONS_PATH`` when set (tests use this); otherwise
    falls back to the runtime memory registry record so that Memory and
    Runtime share one source of truth.
    """
    override = os.environ.get("AISWMM_LESSONS_PATH")
    if override:
        return Path(override)
    return repo_root() / "memory" / "modeling-memory" / "lessons_learned.md"


def _rag_index_dir() -> Path:
    override = os.environ.get("AISWMM_RAG_DIR")
    if override:
        return Path(override)
    return repo_root() / "memory" / "rag-memory"


def _recall_memory_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    from agentic_swmm.memory.context_fence import wrap as _wrap_fence
    from agentic_swmm.memory.recall import recall as _recall

    pattern = str(call.args.get("pattern") or "").strip()
    if not pattern:
        return _failure(call, "pattern is required")

    section = _recall(pattern, _lessons_path())
    if not section:
        wrapped = _wrap_fence("", source="lessons", stale=False)
        return {
            "tool": call.name,
            "args": call.args,
            "ok": True,
            "results": {"pattern": pattern, "layer": "curated", "found": False},
            "excerpt": wrapped,
            "chars": len(wrapped),
            "summary": f"recall_memory: no match for pattern '{pattern}'",
        }

    truncated = _truncate_to_token_budget(section, _RECALL_PATTERN_TOKEN_BUDGET)
    wrapped = _wrap_fence(truncated, source="lessons", stale=False)
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": {"pattern": pattern, "layer": "curated", "found": True},
        "excerpt": wrapped,
        "chars": len(wrapped),
        "summary": f"recall_memory: matched '{pattern}' ({_estimated_tokens(truncated)} est. tokens)",
    }


def _recall_memory_search_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    from agentic_swmm.memory.context_fence import wrap as _wrap_fence

    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    top_k = int(call.args.get("top_k") or 3)

    try:
        from agentic_swmm.memory.recall_search import recall_search as _recall_search
    except Exception as exc:
        return _failure(call, f"recall_memory_search backend unavailable: {exc}")

    index_dir = _rag_index_dir()
    corpus_path = index_dir / "corpus.jsonl"
    lessons_path = _lessons_path()

    try:
        results = _recall_search(
            query,
            top_k=top_k,
            index_dir=index_dir,
            corpus_path=corpus_path,
            lessons_path=lessons_path,
        )
    except Exception as exc:
        return _failure(call, f"recall_memory_search failed: {exc}")

    stale = any(bool(result.get("warning")) for result in results)
    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    truncated = _truncate_to_token_budget(rendered, _RECALL_SEARCH_TOKEN_BUDGET)
    wrapped = _wrap_fence(truncated, source="rag", stale=stale)
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": results,
        "excerpt": wrapped,
        "chars": len(wrapped),
        "summary": f"recall_memory_search: {len(results)} hit(s) for query (stale={stale})",
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
    from agentic_swmm.memory import facts as _facts_mod

    text = str(call.args.get("text") or "").strip()
    if not text:
        return _failure(call, "text is required")
    source_id = call.args.get("source_session_id")
    source_id = str(source_id).strip() if isinstance(source_id, str) and source_id.strip() else None
    try:
        staging_path = _facts_mod.record_fact_to_staging(
            text, source_session_id=source_id
        )
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
    "_recall_memory_search_tool",
    "_recall_session_history_tool",
    "_record_fact_tool",
]
