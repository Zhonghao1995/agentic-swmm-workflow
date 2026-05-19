"""Memory-informed disambiguation policy (PRD-07 Phase 3).

The runtime's intent layer historically calls the LLM (or hard-coded
rules) without consulting memory. This module adds the pure-function
policy layer that resolves a user utterance against the read-side
:class:`MemoryContext` from Phase 1 and decides which of the four
confidence quadrants applies.

Why a pure function (no I/O, no LLM)
------------------------------------
The policy is the *deterministic* slice of disambiguation: given the
same utterance + memory snapshot, the decision is identical every
time. That lets the calling planner short-circuit cheap cases
(``auto_complete``), pre-fill the user-confirmation surface
(``memory_informed``), or fall through to the existing LLM path
(``llm``) without entangling the decision logic with provider calls
or filesystem reads.

The four quadrants (stakes × evidence)
--------------------------------------
``auto_complete``
    Utterance is unambiguous against memory (exactly one matching
    case, or one explicit case-name token that matches a hit).
    The planner can skip the LLM and proceed.

``memory_informed``
    Multiple candidates exist; memory ranks them by recency. The
    planner pre-fills the confirmation prompt with the top-1 but
    still asks the user.

``llm``
    Memory was consulted but not decisive (zero hits, or an explicit
    token that does not appear in memory). Defer to the existing
    LLM / keyword fallback.

``hitl``
    High-stakes verb with zero matching evidence. Raise a blocking
    prompt to the user before doing anything irreversible.

Failure mode
------------
The policy never raises on data shape. An empty
:class:`MemoryContext` is the most common input (first run on a
fresh project) and yields ``confidence="llm"`` for low stakes or
``confidence="hitl"`` for high stakes. Callers wire the
:class:`MemoryHITLRequired` exception only on the hitl branch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentic_swmm.agent.memory_context import MemoryContext, ParametricRecord


# Stakes labels callers may pass in. Kept small on purpose — the four
# quadrants already encode evidence strength, so stakes only needs two
# levels (everything routine vs. anything that mutates memory/ or
# accepts a calibration). Adding more buckets would push the decision
# matrix from 2×2 into a fuzzy multi-dimensional grid for negligible
# expressive gain.
VALID_STAKES: tuple[str, ...] = ("low", "high")


# Tokens that, when present in a user utterance, identify the verb
# being requested. The set is deliberately tiny — the disambiguator's
# job is not to be an NLU layer, just to find an explicit case-name
# token within the prompt. The actual intent classification still
# runs upstream/downstream.
_CASE_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")


# Bare verbs / very common English words that we should never treat
# as "the user typed an explicit case name". Kept tiny — we only need
# to exclude the verbs the existing intent layer already understands,
# plus a handful of glue words that the case-name regex would
# otherwise admit. Anything domain-specific (watershed names,
# project codes) is never on this list.
_VERB_BLOCKLIST: frozenset[str] = frozenset(
    {
        # SWMM action verbs
        "run", "runs", "running",
        "audit", "audits", "auditing",
        "plot", "plots", "plotting",
        "calibrate", "calibration", "calibrations",
        "uncertainty", "sensitivity",
        "demo", "acceptance",
        "memory", "summarize", "summarise", "summary",
        "compare", "comparison",
        "accept", "accepts",
        # Glue / filler
        "the", "and", "for", "with", "from", "this", "that",
        "please", "next", "again", "now", "show", "list",
        "case", "cases", "model", "models",
    }
)


class MemoryHITLRequired(Exception):
    """Raised to signal the runtime must stop for human input.

    The runtime catches this exception, surfaces ``args[0]`` (the
    escalation prompt) to the user, and waits. We use an exception
    rather than a return code so the high-stakes path is impossible
    to forget: a caller that ignores the policy result still cannot
    silently proceed past a hitl decision.
    """


@dataclass(frozen=True)
class PolicyDecision:
    """The pure-function output of :func:`decide_with_memory`.

    Frozen because callers (and the trace logger) need a stable
    snapshot — once the policy has spoken, downstream code should not
    be able to scribble on the decision dataclass.
    """

    confidence: str
    resolved_case: str | None
    candidates: list[str] = field(default_factory=list)
    reasoning: str = ""
    escalation: str | None = None


def _normalise_case_name(name: str) -> str:
    """Lower-case + strip non-alphanumerics for token matching.

    Case-name conventions in this project mix dashes, underscores,
    and case ("saanich-b8" vs "Saanich_B8"). The disambiguator must
    treat those as the same identifier when matching against the
    user's typed prompt.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _utterance_tokens(utterance: str) -> list[str]:
    """Return normalised tokens that look like case-name candidates.

    Drops common SWMM verbs and English glue words so that
    "run audit" → ``[]`` (the policy then treats the utterance as
    name-less and runs the ranking branch). A token survives only
    when its lowercase form is not in :data:`_VERB_BLOCKLIST`.
    """
    raw = _CASE_NAME_PATTERN.findall(utterance or "")
    out: list[str] = []
    for token in raw:
        if token.lower() in _VERB_BLOCKLIST:
            continue
        normalised = _normalise_case_name(token)
        if not normalised:
            continue
        out.append(normalised)
    return out


def _explicit_case_match(
    utterance: str, hits: list[ParametricRecord]
) -> ParametricRecord | None:
    """Return a hit whose case_name appears verbatim in the utterance.

    Match is performed on normalised tokens so "run audit on
    Saanich-B8" matches a hit with case_name="saanich-b8". When more
    than one case_name token matches we return ``None`` — the
    utterance is itself ambiguous and the broader ranking logic
    should run.
    """
    tokens = set(_utterance_tokens(utterance))
    if not tokens:
        return None
    matched: list[ParametricRecord] = []
    seen_cases: set[str] = set()
    for hit in hits:
        norm = _normalise_case_name(hit.case_name)
        if norm and norm in tokens and norm not in seen_cases:
            matched.append(hit)
            seen_cases.add(norm)
    if len(matched) == 1:
        return matched[0]
    return None


def _explicit_case_token_present(utterance: str) -> bool:
    """True when the utterance contains a multi-letter alphanumeric token.

    Used to distinguish "user typed a name we don't know about" from
    "user gave us only a verb, no name at all". The first bucket
    falls through to ``llm``; the second triggers our ranking logic.
    """
    return bool(_utterance_tokens(utterance))


def _rank_candidates_by_recency(
    hits: list[ParametricRecord],
) -> list[ParametricRecord]:
    """Sort hits by ``recorded_utc`` descending, breaking ties on run_id.

    Hits without ``recorded_utc`` sort to the end so unannotated rows
    never beat a freshly-timestamped one. The tiebreak on run_id keeps
    the order deterministic across processes — important for the
    audit trail to be reproducible run-over-run.
    """

    def _key(h: ParametricRecord) -> tuple[int, str, str]:
        ts = (h.recorded_utc or "").strip()
        # Empty timestamp sorts to the *end* (lowest priority); valid
        # timestamps sort descending alphabetically (ISO8601 strings
        # sort correctly as strings).
        return (0 if ts else 1, ts, h.run_id)

    # Stable sort: keys 0 (has-ts) all come before keys 1 (no-ts).
    # Within group 0 we want the *largest* ts first, so reverse=True
    # — but only within that group. Sort twice for clarity.
    with_ts = sorted(
        (h for h in hits if (h.recorded_utc or "").strip()),
        key=lambda h: (h.recorded_utc or "", h.run_id),
        reverse=True,
    )
    without_ts = sorted(
        (h for h in hits if not (h.recorded_utc or "").strip()),
        key=lambda h: h.run_id,
    )
    return [*with_ts, *without_ts]


def decide_with_memory(
    utterance: str,
    memory_context: MemoryContext,
    *,
    stakes: str = "low",
) -> PolicyDecision:
    """Return a :class:`PolicyDecision` for the given utterance + memory.

    The function is **pure**: no I/O, no provider call, no mutation.
    Given the same inputs it returns the same decision.

    Arguments:
        utterance: The raw user goal/prompt as typed.
        memory_context: The Phase 1 read snapshot of relevant memory.
            Typically gathered by ``gather_memory_context``. Empty
            contexts are normal and yield ``llm`` or ``hitl``.
        stakes: ``"low"`` (default) for ordinary verbs;
            ``"high"`` for verbs that mutate ``memory/`` or accept a
            calibration. High stakes + zero evidence escalates to
            ``hitl``. Anything outside :data:`VALID_STAKES` raises
            :class:`ValueError` so the call site cannot invent a
            third bucket implicitly.

    Decision tree (high-to-low priority):
        1. ``stakes="high"`` + zero parametric hits → ``hitl``.
        2. Exactly one parametric hit → ``auto_complete`` with that hit.
        3. Explicit case-name token matches exactly one hit →
           ``auto_complete`` with that hit.
        4. Explicit case-name token in utterance but no match →
           ``llm`` (LLM/keyword fallback handles unknown names).
        5. ≥2 hits, no explicit token → ``memory_informed``,
           candidates ranked by recency.
        6. Zero hits, no explicit token → ``llm``.
    """
    if stakes not in VALID_STAKES:
        raise ValueError(
            f"stakes must be one of {VALID_STAKES}; got {stakes!r}"
        )

    hits = list(memory_context.parametric_hits)

    # Rule 1: high stakes + zero evidence → hitl. We escalate before
    # any other branch because high-stakes mistakes are the failure
    # mode the quadrant was added to prevent. Even one hit is enough
    # to keep us out of hitl (the rules below will pick the right
    # downstream bucket).
    if stakes == "high" and not hits:
        return PolicyDecision(
            confidence="hitl",
            resolved_case=None,
            candidates=[],
            reasoning=(
                "high-stakes action requested but memory has zero "
                "matching parametric records"
            ),
            escalation=(
                "This action mutates memory or accepts a calibration "
                "but no prior runs exist to anchor the decision. "
                "Please confirm explicitly before proceeding."
            ),
        )

    # Rule 2: a single matching hit auto-resolves regardless of
    # whether the utterance named it explicitly. The caller already
    # filtered memory by case (gather_memory_context), so one row
    # means one answer.
    if len(hits) == 1:
        only = hits[0]
        return PolicyDecision(
            confidence="auto_complete",
            resolved_case=only.case_name,
            candidates=[only.case_name],
            reasoning=(
                f"single parametric hit for case {only.case_name!r}"
            ),
        )

    # Rule 3: explicit case-name token matches exactly one hit
    # (when there are 2+ hits but only one carries the typed token).
    if hits:
        explicit = _explicit_case_match(utterance, hits)
        if explicit is not None:
            return PolicyDecision(
                confidence="auto_complete",
                resolved_case=explicit.case_name,
                candidates=[explicit.case_name],
                reasoning=(
                    "utterance names case "
                    f"{explicit.case_name!r} explicitly and "
                    "memory has a matching record"
                ),
            )

    # Rule 4: explicit token present but no memory match → defer.
    # The LLM/keyword fallback may know about a case that hasn't
    # been recorded yet, so we should not auto-resolve to a stale
    # candidate just because memory has rows for other cases.
    if _explicit_case_token_present(utterance) and not _explicit_case_match(
        utterance, hits
    ):
        # Sub-case: zero hits anywhere — still defer to LLM.
        return PolicyDecision(
            confidence="llm",
            resolved_case=None,
            candidates=[h.case_name for h in hits],
            reasoning=(
                "utterance mentions a token that does not appear in "
                "memory; deferring to LLM/keyword fallback"
            ),
        )

    # Rule 5: ≥2 hits, no explicit utterance token to disambiguate
    # them. Rank by recency and propose the top-1 as a pre-fill;
    # leave the final confirmation to the caller's existing UX.
    if len(hits) >= 2:
        ranked = _rank_candidates_by_recency(hits)
        return PolicyDecision(
            confidence="memory_informed",
            resolved_case=ranked[0].case_name,
            candidates=[h.case_name for h in ranked],
            reasoning=(
                f"{len(ranked)} candidates in memory; "
                "ranked by recency, top-1 pre-filled for confirmation"
            ),
        )

    # Rule 6: zero hits and no explicit token. Memory has nothing to
    # say; defer entirely to the LLM/keyword fallback.
    return PolicyDecision(
        confidence="llm",
        resolved_case=None,
        candidates=[],
        reasoning="memory has no matching records; deferring to LLM",
    )


__all__ = [
    "MemoryHITLRequired",
    "PolicyDecision",
    "VALID_STAKES",
    "decide_with_memory",
]
