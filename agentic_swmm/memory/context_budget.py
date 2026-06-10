"""Hard character budget for the session-start memory injection block.

The injected memory block (facts + startup memory files + any recall
hints) can grow unbounded as the project accumulates context.  This
module enforces a configurable character cap so the system prompt stays
within a predictable cost envelope.

Key design constraints (from PRD P0-1):
- The **store is never modified** — budgeting is selection-time only.
- Over-budget entries are excluded silently from the injected block but
  remain fully accessible via on-demand recall tools.
- The injected block ends with a one-line note so the model knows
  additional entries exist and can be retrieved.
- A session-trace event records how many entries were excluded and which
  identifiers were omitted.

Terminology used here:
``entry``  — one logical chunk of memory text (e.g., one startup memory
            file, one parametric hit, or one fact block).  Each entry
            carries an identifier and a text body.
``budget`` — the total character limit for the combined injected block.

Usage (session bootstrap path)::

    entries = [
        MemoryEntry(id="facts.md", text="... curated facts ..."),
        MemoryEntry(id="parametric/run-42", text="... recall hit ..."),
    ]
    result = apply_context_budget(entries, budget=4000)
    injected_text = result.injected_text
    # Emit trace event elsewhere using result.excluded_count and
    # result.excluded_ids.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Default character budget for the combined injected memory block.  The
# PRD specifies 4,000 chars for aiswmm (SWMM context tends to be denser
# than generic chat-agent memory).  Configurable via
# ``memory.context_budget_chars`` in ``~/.aiswmm/config.toml``.
DEFAULT_CONTEXT_BUDGET_CHARS = 4000

# The availability note appended when entries are excluded.  Kept short
# so it barely touches the budget.
_AVAILABILITY_NOTE_TEMPLATE = "({n} more memory entries available — recall on demand)"


@dataclass(frozen=True)
class MemoryEntry:
    """One logical unit of memory to be considered for injection.

    Attributes
    ----------
    id:
        Short stable identifier for this entry (used in trace events).
        Examples: ``"facts.md"``, ``"parametric/run-42"``,
        ``"startup/parametric_memory.md"``.
    text:
        The full text body to inject if selected.
    relevance:
        Optional relevance score from a recall path (higher = more
        relevant).  When the store produces no score, pass ``0.0`` —
        entries with equal relevance preserve their input order.
    """

    id: str
    text: str
    relevance: float = 0.0


@dataclass
class BudgetResult:
    """Output of :func:`apply_context_budget`.

    Attributes
    ----------
    injected_text:
        The combined text ready for insertion into the system prompt.
        Includes the availability note when entries were excluded.
        Empty string when no entries fit.
    injected_ids:
        Identifiers of entries whose full text was injected.
    excluded_ids:
        Identifiers of entries that did not fit in the budget.
    excluded_count:
        ``len(excluded_ids)`` — convenience for trace emission.
    truncated_head:
        ``True`` when the *first* entry was larger than the entire
        budget and was head-truncated.  The availability note still
        appears in that case.
    """

    injected_text: str
    injected_ids: list[str] = field(default_factory=list)
    excluded_ids: list[str] = field(default_factory=list)
    excluded_count: int = 0
    truncated_head: bool = False


def apply_context_budget(
    entries: list[MemoryEntry],
    budget: int = DEFAULT_CONTEXT_BUDGET_CHARS,
) -> BudgetResult:
    """Pack ``entries`` greedily into ``budget`` characters.

    Sorting: entries are ranked by ``relevance`` descending, then by
    input order for ties (stable sort preserves caller order when all
    relevance values are equal, which matches the "passthrough" contract
    when the store provides no relevance signal).

    Over-budget behaviour:
    - Entries that do not fit are excluded entirely (no partial
      injection, except for the single-giant-entry edge case below).
    - The injected block ends with an availability note of the form
      ``(N more memory entries available — recall on demand)``.

    Single-giant-entry edge case:
    When the first ranked entry is larger than ``budget`` minus the
    availability note, its text is head-truncated to the remaining
    space, and ``truncated_head=True`` is set in the result.

    Parameters
    ----------
    entries:
        Memory entries to consider.  Empty list → empty result.
    budget:
        Hard character cap.  Values ≤ 0 are treated as unlimited
        (returns all entries joined without a note).

    Returns
    -------
    BudgetResult
        Packed result.  Call :func:`emit_budget_trace_event` with this
        result to write the exclusion record to ``agent_trace.jsonl``.
    """
    if not entries:
        return BudgetResult(injected_text="", injected_ids=[], excluded_ids=[], excluded_count=0)

    # Unlimited budget — passthrough, preserve order, no note.
    if budget <= 0:
        return BudgetResult(
            injected_text="\n\n".join(e.text for e in entries),
            injected_ids=[e.id for e in entries],
            excluded_ids=[],
            excluded_count=0,
        )

    # Sort by relevance descending; Python sort is stable so equal-
    # relevance entries preserve their original order.
    ranked = sorted(entries, key=lambda e: e.relevance, reverse=True)

    # Reserve space for the availability note (worst case: all excluded).
    note_worst = _AVAILABILITY_NOTE_TEMPLATE.format(n=len(ranked))
    note_len = len(note_worst) + 2  # + 2 for the "\n\n" separator

    usable = budget - note_len
    if usable <= 0:
        # Budget too tiny to fit even one character — inject nothing but
        # still emit the note so the model knows entries exist.
        all_ids = [e.id for e in ranked]
        note = _AVAILABILITY_NOTE_TEMPLATE.format(n=len(ranked))
        return BudgetResult(
            injected_text=note,
            injected_ids=[],
            excluded_ids=all_ids,
            excluded_count=len(all_ids),
        )

    selected_texts: list[str] = []
    selected_ids: list[str] = []
    excluded_ids: list[str] = []
    remaining = usable
    truncated_head = False

    for entry in ranked:
        text = entry.text
        if not text:
            # Zero-length entries count as injected (they do not consume
            # budget and should not be listed as excluded).
            selected_texts.append(text)
            selected_ids.append(entry.id)
            continue
        if not selected_texts and len(text) > remaining:
            # Giant first entry — head-truncate to remaining space.
            head = text[:remaining].rstrip()
            selected_texts.append(head)
            selected_ids.append(entry.id)
            remaining = 0
            truncated_head = True
        elif len(text) <= remaining:
            selected_texts.append(text)
            selected_ids.append(entry.id)
            remaining -= len(text)
        else:
            excluded_ids.append(entry.id)

    # Combine.
    if excluded_ids or truncated_head:
        note = _AVAILABILITY_NOTE_TEMPLATE.format(n=len(excluded_ids) if not truncated_head else len(excluded_ids))
        combined = "\n\n".join(selected_texts) + "\n\n" + note if selected_texts else note
    else:
        combined = "\n\n".join(selected_texts)

    return BudgetResult(
        injected_text=combined,
        injected_ids=selected_ids,
        excluded_ids=excluded_ids,
        excluded_count=len(excluded_ids),
        truncated_head=truncated_head,
    )


def emit_budget_trace_event(
    trace_path: Path,
    result: BudgetResult,
    *,
    budget_chars: int,
) -> None:
    """Append one ``memory_context_budget`` event to ``trace_path``.

    Records:
    - ``event``: ``"memory_context_budget"``
    - ``budget_chars``: the configured limit
    - ``injected_count``: number of entries whose text landed in context
    - ``excluded_count``: number of entries omitted
    - ``excluded_ids``: list of their identifiers
    - ``truncated_head``: whether the largest entry was head-truncated

    The file is created if it does not exist (``"a"`` mode).  Failure is
    not swallowed here — the call site must wrap in try/except if
    best-effort behaviour is wanted.
    """
    payload: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": "memory_context_budget",
        "budget_chars": budget_chars,
        "injected_count": len(result.injected_ids),
        "excluded_count": result.excluded_count,
        "excluded_ids": list(result.excluded_ids),
        "truncated_head": result.truncated_head,
    }
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


__all__ = [
    "DEFAULT_CONTEXT_BUDGET_CHARS",
    "BudgetResult",
    "MemoryEntry",
    "apply_context_budget",
    "emit_budget_trace_event",
]
