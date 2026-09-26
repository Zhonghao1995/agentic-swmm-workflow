"""Keyword recall over the project memory store (memory simplification PR 3b).

The planner's ``recall_memory`` tool used to look a pattern name up in a
generated ``lessons_learned.md``; that file and its generator are gone.
Recall now reads the two ledgers that hold what a project learned the
hard way, through their database tables:

- ``run_failures.jsonl``: tool calls that failed, with the fix that
  worked when one was recorded (the failure loop, #554);
- ``negative_lessons.jsonl``: parameter regions known to be bad.

A hit is a row whose text contains at least one query token; more
distinct tokens score higher, and ties go to the most recent row. Pure
function of the store: no model, no index to rebuild.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agentic_swmm.memory.store import ledger_rows

_WORD_RE = re.compile(r"[a-z0-9_.]{3,}")
_CJK_RE = re.compile(r"[一-鿿]+")


def _tokens(query: str) -> set[str]:
    low = str(query or "").lower()
    tokens = set(_WORD_RE.findall(low))
    for run in _CJK_RE.findall(low):
        if len(run) == 1:
            tokens.add(run)
        tokens.update(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def _failure_text(row: dict[str, Any]) -> str:
    text = f"{row.get('tool', '')} failed: {row.get('summary', '')}"
    if row.get("fix"):
        text += f"; fixed by: {row['fix']}"
    return text


def _lesson_text(row: dict[str, Any]) -> str:
    tried = row.get("parameters_tried")
    tried_text = json.dumps(tried, sort_keys=True, ensure_ascii=False) if tried else "-"
    return (
        f"{row.get('lesson_type', '')} on {row.get('case_name', '')}: {row.get('note', '')}; "
        f"parameters tried: {tried_text}"
    )


def _hit(kind: str, row: dict[str, Any], text: str, tokens: set[str], case_name: str | None) -> dict[str, Any] | None:
    haystack = text.lower()
    score = sum(1 for token in tokens if token in haystack)
    if score == 0:
        return None
    run_id = str(row.get("run_id", ""))
    if case_name and case_name.lower() in run_id.lower():
        score += 1
    return {
        "kind": kind,
        "run_id": run_id,
        "case_name": str(row.get("case_name") or ""),
        "recorded_at": str(row.get("recorded_at") or row.get("recorded_utc") or ""),
        "score": score,
        "text": text,
    }


def recall(
    query: str,
    memory_dir: Path,
    *,
    case_name: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` store rows relevant to ``query``, best first.

    Failures are project-wide (a case name only boosts rows whose run id
    carries it); negative lessons are filtered to the case when one is
    given. An empty query or an empty store yields ``[]``.
    """
    tokens = _tokens(query)
    if not tokens:
        return []
    memory_dir = Path(memory_dir)
    hits: list[dict[str, Any]] = []
    for row in ledger_rows(memory_dir / "run_failures.jsonl"):
        hit = _hit("failure", row, _failure_text(row), tokens, case_name)
        if hit:
            hits.append(hit)
    for row in ledger_rows(memory_dir / "negative_lessons.jsonl", case_name=case_name):
        hit = _hit("negative_lesson", row, _lesson_text(row), tokens, case_name)
        if hit:
            hits.append(hit)
    hits.sort(key=lambda hit: hit["recorded_at"], reverse=True)
    hits.sort(key=lambda hit: hit["score"], reverse=True)
    return hits[: max(1, int(limit or 5))]
