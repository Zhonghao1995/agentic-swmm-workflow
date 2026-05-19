"""Session-history recall for memory-informed disambiguation (Round 6).

PRD-07 Phase 3 specifies two halves of memory-informed disambiguation:
the *case-based* recall (already wired by Round 1, see
:func:`agentic_swmm.agent.memory_context.gather_memory_context`) and
the *prompt-history-based* recall implemented here.

The intent is "skip LLM disambiguation when the user has historically
meant X when saying similar things". The runtime already writes one
``memory_informed_decision`` line per disambiguation onto
``agent_trace.jsonl``; this module reads those lines back and surfaces
a consensus value when a strong majority of recent decisions on the
same field agreed.

Why token-overlap Jaccard (no embedding dependency)
---------------------------------------------------
At Round 6 scale the agent trace carries a few dozen decisions per
session. A 1.5MB embedding model would dwarf the dependency surface
for a recall path that only needs to ask "did the last few similar
utterances agree on a value?". Lowercased word-token Jaccard with a
0.5 threshold answers that cleanly without any new dep — and when
both utterances are short (< 4 tokens) we fall back to a substring
check so case names like "todcreek" still match.

Failure mode
------------
Every read path is best-effort. A missing trace dir, a missing file,
a torn final line, an unparsable event — all collapse to "no
matches", never an exception. Memory must never break dispatch.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Default trace filename inside ``trace_dir``. Mirrors the convention
# established in ``reporting.py`` (every other agent_trace consumer
# uses the same filename).
_AGENT_TRACE_FILENAME = "agent_trace.jsonl"


# Token regex shared with ``memory_informed_policy``. Multi-character
# alphanumeric runs; ignores punctuation. We do not import the regex
# from that module because the two have independent reasons to drift.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-]*")


@dataclass(frozen=True)
class PriorResolution:
    """One historical ``memory_informed_decision`` row.

    Frozen because the policy treats each resolution as immutable
    evidence — once read from disk, it should not be possible for a
    downstream caller to scribble on the value the user originally
    confirmed.
    """

    utterance: str
    decision_point: str
    field: str
    value: Any
    confidence: str
    timestamp: str
    run_id: str | None = None


@dataclass(frozen=True)
class SessionHistoryRecall:
    """The pure-function output of :func:`recall_session_history`.

    ``consensus_value`` is non-None iff a strong majority (by default
    66%) of ``similar_resolutions`` agreed on the same value for the
    *same* ``field``. ``consensus_confidence`` is the fraction of
    similar resolutions that picked the winner, computed against
    ``evidence_count`` (i.e. ``len(similar_resolutions)``).
    """

    utterance_query: str
    similar_resolutions: list[PriorResolution] = field(default_factory=list)
    consensus_value: Any | None = None
    consensus_field: str | None = None
    evidence_count: int = 0
    consensus_confidence: float = 0.0


def _tokenize(utterance: str) -> set[str]:
    """Return lowercased word tokens for similarity comparison."""
    return set(_TOKEN_RE.findall((utterance or "").lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    """Standard set Jaccard; empty sets collapse to 0 to be conservative."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return inter / union


def _is_similar(query: str, candidate: str) -> bool:
    """Return True when ``candidate`` should count as evidence for ``query``.

    Primary path: lowercased token Jaccard ≥ 0.5.

    Short-utterance fallback: when *both* utterances have fewer than 4
    tokens, also accept a case-insensitive substring match in either
    direction. This rescues the "user typed only the case name"
    branch — e.g. previous "todcreek" matches new "todcreek" even
    though the token sets are size-1.
    """
    q_tokens = _tokenize(query)
    c_tokens = _tokenize(candidate)
    if _jaccard(q_tokens, c_tokens) >= 0.5:
        return True
    if len(q_tokens) < 4 and len(c_tokens) < 4:
        q_low = (query or "").strip().lower()
        c_low = (candidate or "").strip().lower()
        if q_low and c_low and (q_low in c_low or c_low in q_low):
            return True
    return False


def _read_decision_rows(trace_path: Path) -> list[dict[str, Any]]:
    """Stream ``memory_informed_decision`` rows from ``trace_path``.

    Torn final lines and non-decision events are silently skipped.
    Missing files yield ``[]``.
    """
    if not trace_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with trace_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("event") != "memory_informed_decision":
                    continue
                rows.append(row)
    except OSError:
        return []
    return rows


def _row_to_resolution(row: dict[str, Any]) -> PriorResolution | None:
    """Project one JSONL row into a :class:`PriorResolution`.

    Required fields: ``utterance`` (or empty string), ``field``,
    ``value_chosen``. Missing required fields → ``None``. The function
    is tolerant of extra fields the writer may add later.
    """
    if not isinstance(row, dict):
        return None
    f = row.get("field")
    if not isinstance(f, str) or not f:
        return None
    utterance = row.get("utterance") or ""
    decision_point = row.get("decision_point") or "intent_disambiguate"
    confidence = row.get("confidence") or "memory_informed"
    timestamp = (
        row.get("timestamp_utc")
        or row.get("timestamp")
        or ""
    )
    run_id = row.get("run_id")
    if not isinstance(run_id, str):
        run_id = None
    return PriorResolution(
        utterance=str(utterance),
        decision_point=str(decision_point),
        field=str(f),
        value=row.get("value_chosen"),
        confidence=str(confidence),
        timestamp=str(timestamp),
        run_id=run_id,
    )


def _compute_consensus(
    resolutions: list[PriorResolution],
    *,
    threshold: float,
) -> tuple[Any | None, str | None, float]:
    """Return (value, field, confidence) when a single field has a
    strong majority winner.

    The function selects the most-represented (field, value) pair
    among ``resolutions`` and reports it iff its share of the total
    sample reaches ``threshold``. Ties (multiple equally-represented
    values for the same field) fall through to "no consensus" — the
    caller must defer to the LLM when the history itself is split.
    """
    if not resolutions:
        return None, None, 0.0
    counts: dict[tuple[str, Any], int] = {}
    for r in resolutions:
        # Values may be unhashable in pathological cases; coerce to
        # JSON-serialised form for the count key.
        try:
            key = (r.field, r.value)
            hash(key)
        except TypeError:
            key = (r.field, json.dumps(r.value, sort_keys=True))
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None, None, 0.0
    # Find the top pair. Sort by (-count, field, str(value)) so the
    # result is deterministic when two pairs tie.
    sorted_pairs = sorted(
        counts.items(),
        key=lambda kv: (-kv[1], kv[0][0], json.dumps(kv[0][1], default=str)),
    )
    (top_field, top_value), top_count = sorted_pairs[0]
    if len(sorted_pairs) > 1 and sorted_pairs[1][1] == top_count:
        # Two values tied for first — no clear winner.
        return None, None, 0.0
    total = len(resolutions)
    share = top_count / total if total else 0.0
    if share < threshold:
        return None, None, share
    return top_value, top_field, share


def recall_session_history(
    *,
    utterance: str,
    decision_point: str | None = None,
    user_id: str | None = None,
    limit: int = 10,
    trace_dir: Path | None = None,
    consensus_threshold: float = 0.66,
) -> SessionHistoryRecall:
    """Return a recall snapshot for ``utterance`` against prior decisions.

    Arguments:
        utterance: The current user utterance the runtime is about to
            disambiguate. Lowercased and tokenised inside the helper.
        decision_point: When set, only prior rows tagged with the same
            ``decision_point`` (e.g. ``"intent_disambiguate"``) are
            considered. When ``None``, all decision points match.
        user_id: Reserved for future per-user separation; today the
            agent trace already lives per-session, so we accept the
            argument for API stability and currently do not partition
            on it.
        limit: Cap on the number of resolutions returned. The most
            recent matches (by timestamp / file order) win when more
            than ``limit`` candidates qualify.
        trace_dir: Directory holding ``agent_trace.jsonl``. ``None``
            yields an empty recall (used by legacy callers that have
            no trace path to inject).
        consensus_threshold: Fraction of matching resolutions that
            must agree on a (field, value) for the recall to surface
            a consensus. Default 0.66 matches PRD §8.

    Returns:
        A :class:`SessionHistoryRecall`. ``consensus_value`` is
        non-None iff a winner cleared ``consensus_threshold`` and was
        not tied with another value on the same field. The function
        never raises on filesystem errors or malformed rows.
    """
    empty = SessionHistoryRecall(
        utterance_query=utterance,
        similar_resolutions=[],
        consensus_value=None,
        consensus_field=None,
        evidence_count=0,
        consensus_confidence=0.0,
    )

    if trace_dir is None:
        return empty
    trace_dir_path = Path(trace_dir)
    if not trace_dir_path.is_dir():
        return empty

    rows = _read_decision_rows(trace_dir_path / _AGENT_TRACE_FILENAME)
    if not rows:
        return empty

    candidates: list[PriorResolution] = []
    for row in rows:
        resolved = _row_to_resolution(row)
        if resolved is None:
            continue
        if decision_point is not None and resolved.decision_point != decision_point:
            continue
        if not _is_similar(utterance, resolved.utterance):
            continue
        candidates.append(resolved)

    # Keep most-recent first. Timestamp absent / empty sorts to the end.
    def _sort_key(r: PriorResolution) -> tuple[int, str]:
        ts = (r.timestamp or "").strip()
        return (0 if ts else 1, ts)

    with_ts = sorted(
        (r for r in candidates if (r.timestamp or "").strip()),
        key=lambda r: r.timestamp,
        reverse=True,
    )
    without_ts = [r for r in candidates if not (r.timestamp or "").strip()]
    ordered = [*with_ts, *without_ts]
    capped = ordered[: max(0, int(limit))] if limit is not None else ordered

    value, field_name, share = _compute_consensus(
        capped, threshold=float(consensus_threshold)
    )

    return SessionHistoryRecall(
        utterance_query=utterance,
        similar_resolutions=capped,
        consensus_value=value,
        consensus_field=field_name,
        evidence_count=len(capped),
        consensus_confidence=share,
    )


__all__ = [
    "PriorResolution",
    "SessionHistoryRecall",
    "recall_session_history",
]
