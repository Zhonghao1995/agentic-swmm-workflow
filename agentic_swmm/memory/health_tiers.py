"""Health-tier resolution for modeling-memory entries (PR 4, Phase 1).

Each memory entry is in one of three tiers based on its derived health
score and event history:

- **active**   — health score ≥ 0.40; recalled normally.
- **watch**    — 0.15 ≤ health score < 0.40, or health score ≥ 0.40 but
                 a ``run_failed`` event occurred while the entry was
                 previously in watch.  Recalled but every result carries
                 a machine-readable caution string the planner sees
                 verbatim.
- **archived** — health score < 0.15, OR any ``run_failed`` event while
                 already in watch tier.  Excluded from default recall and
                 from the context-budget packing; restorable via the
                 explicit ``aiswmm memory restore`` verb.

Tier thresholds (all in :data:`_TIER_TUNABLES`)
-----------------------------------------------
active  ≥ 0.40
watch   ≥ 0.15
archived < 0.15  (hard floor)

Hard escalation rule
--------------------
If the most recent health tier resolved as watch AND any of the entry's
scoring events is a ``run_failed`` with ``attribution == "single"``,
the tier is escalated to archived regardless of the numeric score.

Caution message
---------------
For watch-tier results, :func:`watch_caution_message` returns a one-line
string of the form::

    health watch: last applied run scored KGE <value>, below this
    entry's band <band_low>−<tolerance> (see aiswmm memory health <id>)

The message is built from the most recent negative event with a metric
value (``below_band`` first; ``run_failed`` as fallback with no metric).
When no negative event exists but the score is still in watch range,
a generic phrasing is used.

Scoring integration
-------------------
:func:`recall_score` multiplies a raw relevance score by the entry's
health score.  Entries with no ledger events carry the start health
(0.70) as their multiplier — the equal multiplier preserves ordering
among event-less entries (verified by the neutrality lock-in test in
test_memory_health_tiers.py).
"""

from __future__ import annotations

from typing import Any

from agentic_swmm.memory.memory_outcomes import _HEALTH_TUNABLES, health_score

# ── Tier thresholds ───────────────────────────────────────────────────────────

#: All tier-threshold parameters.  Live here so downstream tooling can replay
#: the same ledger under different thresholds without changing code.
_TIER_TUNABLES: dict[str, float] = {
    "active_threshold": 0.40,
    "watch_threshold": 0.15,
}


# ── Public API ────────────────────────────────────────────────────────────────


def health_tier(
    memory_id: str,
    events: list[dict[str, Any]],
) -> str:
    """Resolve the health tier for ``memory_id`` from its ledger events.

    Parameters
    ----------
    memory_id:
        The memory entry id (e.g. ``"pm-abc123"``).
    events:
        All ledger events for ``memory_id`` (pre-filtered by the caller
        using :func:`agentic_swmm.memory.memory_outcomes.events_for_memory`,
        or the full ledger — this function filters internally).

    Returns
    -------
    str
        ``"active"``, ``"watch"``, or ``"archived"``.
    """
    score = health_score(memory_id, events)
    active_threshold = _TIER_TUNABLES["active_threshold"]
    watch_threshold = _TIER_TUNABLES["watch_threshold"]

    if score < watch_threshold:
        return "archived"

    if score < active_threshold:
        # watch tier — check the hard escalation rule
        if _has_run_failed_in_watch(memory_id, events):
            return "archived"
        return "watch"

    # score >= active_threshold — still check escalation: if the entry
    # crossed from watch to active via positive events but still has a
    # run_failed event that fired while it was in watch, we escalate.
    # (Rule: any run_failed while in watch → archived, score independent.)
    if _has_run_failed_in_watch(memory_id, events):
        return "archived"

    return "active"


def _has_run_failed_in_watch(
    memory_id: str,
    events: list[dict[str, Any]],
) -> bool:
    """Return True if any single-attribution run_failed event exists for this entry.

    The PRD rule is: any ``run_failed`` event while already in watch →
    archived.  We implement this as: at the point any ``run_failed`` event
    (attribution == "single") appears in the ledger, re-derive the score
    from events *up to but not including* that event, and check if it was
    in watch at that moment.
    """
    active_threshold = _TIER_TUNABLES["active_threshold"]
    watch_threshold = _TIER_TUNABLES["watch_threshold"]

    # Collect only events for this memory id
    my_events = [
        e for e in events
        if e.get("memory_id") == memory_id
    ]

    for i, ev in enumerate(my_events):
        if ev.get("event") == "run_failed" and ev.get("attribution") == "single":
            # Score at the moment just before this event
            prior_events = my_events[:i]
            prior_score = health_score(memory_id, prior_events)
            if watch_threshold <= prior_score < active_threshold:
                return True

    return False


def recall_score(
    relevance: float,
    memory_id: str,
    events: list[dict[str, Any]],
) -> float:
    """Multiply ``relevance`` by the entry's health score.

    Entries with no ledger events carry the start health (0.70), so equal
    relevance values produce equal final scores — ordering is preserved.

    Parameters
    ----------
    relevance:
        Raw relevance score from the retrieval path (any positive float).
    memory_id:
        The memory entry id.
    events:
        All ledger events (full ledger or pre-filtered).

    Returns
    -------
    float
        ``relevance × health_score(memory_id, events)``.
    """
    h = health_score(memory_id, events)
    return relevance * h


def watch_caution_message(
    memory_id: str,
    events: list[dict[str, Any]],
) -> str:
    """Build the one-line watch caution string for the planner.

    The message quotes the most recent negative event's metric numbers so
    the planner has quantified evidence.  Format::

        health watch: last applied run scored KGE <value>, below this
        entry's band <band_low>−<tolerance> (see aiswmm memory health <id>)

    When no negative event with a metric exists, falls back to a generic
    phrasing.

    Parameters
    ----------
    memory_id:
        The memory entry id.
    events:
        All ledger events (full ledger or pre-filtered).

    Returns
    -------
    str
        One-line caution string (no trailing newline).
    """
    tolerance = _HEALTH_TUNABLES["below_band_tolerance"]

    # Find the most recent below_band event with a real metric value.
    my_events = [
        e for e in reversed(events)
        if e.get("memory_id") == memory_id
    ]

    for ev in my_events:
        if ev.get("event") == "below_band" and ev.get("attribution") == "single":
            m = ev.get("metric") or {}
            value = m.get("value")
            band_low = m.get("band_low")
            if value is not None and band_low is not None:
                return (
                    f"health watch: last applied run scored KGE {value:.2f}, "
                    f"below this entry's band {band_low + tolerance:.2f}"
                    f"−{tolerance:.2f} "
                    f"(see aiswmm memory health {memory_id})"
                )

    # Fallback: run_failed without a metric
    for ev in my_events:
        if ev.get("event") == "run_failed" and ev.get("attribution") == "single":
            return (
                f"health watch: last applied run failed; "
                f"apply with caution "
                f"(see aiswmm memory health {memory_id})"
            )

    # Generic: in watch range but no disqualifying event recorded yet
    h = health_score(memory_id, events)
    return (
        f"health watch: entry health score {h:.2f} is below the active "
        f"threshold {_TIER_TUNABLES['active_threshold']:.2f} "
        f"(see aiswmm memory health {memory_id})"
    )


__all__ = [
    "_TIER_TUNABLES",
    "health_tier",
    "recall_score",
    "watch_caution_message",
]
