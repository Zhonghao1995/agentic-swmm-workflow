"""New-case onboarding chat flow (Round 7 / PRD-07 Phase 5).

When the user kicks off a new case the agent should not silently fall
back to generic defaults. The ``aiswmm transfer`` CLI verb already
computes warm-start recommendations from cross-watershed history, but
nothing surfaces those recommendations *automatically* — the modeler
has to remember to invoke ``transfer`` manually.

Round 7 wires the surface into the workflow-mode adapters: every
runnable adapter calls :func:`maybe_offer_onboarding` at the top of
``run`` and, if the gate fires, raises a :class:`MemoryHITLRequired`
whose ``decision_point="new_case_onboarding"`` instructs
``runtime.py`` to render the embedded chat block directly (it already
contains the structured Y/n/customize prompt).

Why a HITL surface
------------------
Onboarding is an *advisory* moment: the agent has evidence (similar
calibrated cases on file) but cannot ethically apply the parameter
set without the user agreeing. The HITL exception is the existing
way the runtime stops to ask for input; reusing it keeps the chat
block consistent with every other HITL escalation.

Latching
--------
We do not need a per-session "already asked" flag. The gate fires
only when ``is_new_case`` returns True — i.e. ``parametric_memory.jsonl``
has *zero* rows for ``case_name``. The moment a run wraps up and
writes the first parametric row, the gate naturally turns off for the
next call. This is the same crash-safety as the rest of the memory
runtime — re-running after a crash sees the durable state and decides
fresh.

Opt-out
-------
Three knobs disable the gate, all of which are existing controls used
elsewhere in the runtime:

* ``AISWMM_DISABLE_MEMORY_INFORMED=1`` — environment flag
* ``--ignore-memory`` CLI flag (sets the env var)
* No similar cases on file (``recommend_parameters_for_new_case``
  returns an empty list) → ``triggered=False, reason="no_similar_cases"``

The ``AISWMM_DISABLE_SWMM_GATES`` flag is **not** consulted here; that
flag is for preflight/postflight gates, not memory consultation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_swmm.agent.feature_flags import memory_informed_disabled
from agentic_swmm.memory.cross_watershed_transfer import (
    TransferRecommendation,
    recommend_parameters_for_new_case,
)


# Workflow-intent tokens. A user prompt is treated as *signalling intent
# to act on the case* when it carries at least one of these tokens. The
# list is intentionally short — domain-specific identifiers (case names,
# project codes) are never on it; bare greetings ("hello", "help") are
# never on it.
WORKFLOW_INTENT_TOKENS: frozenset[str] = frozenset(
    {
        "calibrate",
        "calibration",
        "tune",
        "tuning",
        "run",
        "running",
        "audit",
        "simulate",
        "simulation",
        "model",
        "modeling",
        "setup",
        "start",
    }
)

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class OnboardingDecision:
    """One outcome of the onboarding gate.

    ``triggered`` is False when the gate decided not to offer
    onboarding; ``reason`` carries the short label the trace logger
    surfaces (``"new_case"`` / ``"memory_disabled"`` /
    ``"existing_history"`` / ``"no_similar_cases"`` / ``"no_intent"``).
    When ``triggered`` is True the caller raises
    :class:`MemoryHITLRequired` with ``chat_block`` as the message.
    """

    target_case: str
    triggered: bool
    reason: str
    recommendations: list[TransferRecommendation] = field(default_factory=list)
    chat_block: str | None = None


@dataclass
class OnboardingContext:
    """Mutable per-context onboarding state.

    Adapters attach an :class:`OnboardingContext` to
    :class:`WorkflowContext` once the user has answered the HITL chat
    block. The downstream planner / LLM reads ``defaults`` to seed its
    initial proposal, and ``mode`` carries the user's response label so
    a later tool call knows whether to surface a free-form custom path.
    """

    defaults: dict[str, float] = field(default_factory=dict)
    mode: str = "default"
    accepted_source_case: str | None = None


def is_new_case(case_name: str, *, parametric_store: Path) -> bool:
    """True when ``parametric_store`` has zero rows for ``case_name``.

    Missing files count as "no history". A torn-final-line row is
    treated as no-match so a corrupt store cannot accidentally suppress
    the onboarding prompt.
    """
    if not case_name or not case_name.strip():
        return False
    from agentic_swmm.memory.parametric_memory import recall_parametric

    rows = recall_parametric(parametric_store, {"case_name": case_name.strip()})
    return len(rows) == 0


def should_offer_transfer(
    case_name: str,
    utterance: str,
    *,
    parametric_store: Path,
) -> bool:
    """Gate combining new-case + workflow-intent + opt-out checks.

    Returns True iff:

    * The opt-out env flag is unset.
    * The utterance carries a token from
      :data:`WORKFLOW_INTENT_TOKENS` (so a bare "hello" never triggers).
    * The case has zero rows in the parametric store.
    """
    if memory_informed_disabled():
        return False
    if not _utterance_has_intent(utterance):
        return False
    return is_new_case(case_name, parametric_store=parametric_store)


def maybe_offer_onboarding(
    *,
    case_name: str,
    utterance: str,
    target_inp: Path | None,
    parametric_store: Path,
    calibration_store: Path,
    negative_lessons_store: Path,
    storm_library_path: Path,
    benchmarks_path: Path,
    top_k: int = 3,
) -> OnboardingDecision:
    """Compute the onboarding decision for a workflow context.

    When the gate is closed (opt-out, existing case, no intent) the
    decision carries ``triggered=False`` and a short ``reason``. When
    the gate fires the recommender is consulted; if it returns at
    least one recommendation a chat block is rendered and
    ``triggered=True``.

    Defensive: a missing or unparseable ``target_inp`` never raises;
    the decision degrades to ``triggered=False`` with reason
    ``"no_similar_cases"`` so the runtime keeps going.
    """
    target = case_name or ""
    if memory_informed_disabled():
        return OnboardingDecision(
            target_case=target,
            triggered=False,
            reason="memory_disabled",
        )
    if not _utterance_has_intent(utterance):
        return OnboardingDecision(
            target_case=target,
            triggered=False,
            reason="no_intent",
        )
    if not is_new_case(case_name, parametric_store=parametric_store):
        return OnboardingDecision(
            target_case=target,
            triggered=False,
            reason="existing_history",
        )
    if target_inp is None or not Path(target_inp).is_file():
        return OnboardingDecision(
            target_case=target,
            triggered=False,
            reason="no_similar_cases",
        )

    try:
        recs = recommend_parameters_for_new_case(
            Path(target_inp),
            calibration_store=Path(calibration_store),
            top_k=top_k,
            run_dir=None,
            storm_library_path=Path(storm_library_path),
            negative_lessons_store=Path(negative_lessons_store),
            benchmarks_path=Path(benchmarks_path),
        )
    except Exception:
        return OnboardingDecision(
            target_case=target,
            triggered=False,
            reason="no_similar_cases",
        )

    if not recs:
        return OnboardingDecision(
            target_case=target,
            triggered=False,
            reason="no_similar_cases",
        )

    chat_block = format_onboarding_chat_block(target, recs)
    return OnboardingDecision(
        target_case=target,
        triggered=True,
        reason="new_case",
        recommendations=recs,
        chat_block=chat_block,
    )


def format_onboarding_chat_block(
    target_case: str, recs: list[TransferRecommendation]
) -> str:
    """Render the user-facing multi-line chat block.

    The block lists every recommendation with its similarity and any
    objective metric the source calibration carried, then names the
    top-1 source as the recommended starter calibration. The optional
    "Recommended design storm" and "Known pitfall" lines only appear
    when the underlying enrichment is non-empty so we never surface
    the placeholder ``None`` text.
    """
    if not recs:
        # Defensive: the caller should not reach here, but if it does
        # we still want to render something coherent.
        return (
            f'Starting new case "{target_case}". I have no lessons from '
            "similar past cases on file."
        )

    label = target_case or "(unnamed)"
    n = len(recs)
    lines = [
        f'Starting new case "{label}". I have lessons from {n} similar '
        "past case(s):"
    ]
    for rec in recs:
        rec_summary = _format_recommendation_line(rec)
        lines.append(f"  • {rec_summary}")
    top = recs[0]
    lines.append(
        f"Recommended starter calibration: parameters from {top.source_case}."
    )

    storm = top.recommended_design_storm
    if isinstance(storm, dict):
        key = storm.get("key") or "(unnamed storm)"
        lines.append(f"Recommended design storm: {key}.")

    failure_patterns = top.known_failure_patterns or []
    if failure_patterns:
        lines.append(
            f"Known pitfall in similar cases: {len(failure_patterns)} "
            f"lesson(s) from {top.source_case}."
        )

    lines.append("")
    lines.append("Proceed with these defaults? [Y/n/customize]")
    return "\n".join(lines)


def parse_onboarding_response(
    response: str,
) -> str:
    """Classify a user reply to the onboarding chat block.

    Returns one of:

    * ``"accept"`` — empty string or any of ``"Y"``, ``"yes"``, ``"y"``
    * ``"decline"`` — ``"n"``, ``"no"``
    * ``"customize"`` — ``"customize"`` (case-insensitive substring match)
    * ``"unknown"`` — anything else
    """
    raw = (response or "").strip().lower()
    if raw == "":
        return "accept"
    if raw in {"y", "yes"}:
        return "accept"
    if raw in {"n", "no"}:
        return "decline"
    if "customize" in raw or "customise" in raw or raw == "c":
        return "customize"
    return "unknown"


def apply_onboarding_acceptance(
    ctx: Any, decision: OnboardingDecision
) -> OnboardingContext:
    """Attach an :class:`OnboardingContext` reflecting a user accept.

    The top-1 recommendation's parameter set becomes
    ``ctx.onboarding.defaults`` so the downstream planner has a seed.
    Returns the :class:`OnboardingContext` for callers that want to
    inspect it without going through ``ctx.onboarding``.
    """
    if not decision.recommendations:
        onboarding = OnboardingContext()
    else:
        top = decision.recommendations[0]
        onboarding = OnboardingContext(
            defaults=dict(top.proposed_parameters),
            mode="accepted",
            accepted_source_case=top.source_case,
        )
    setattr(ctx, "onboarding", onboarding)
    return onboarding


def mark_customize(ctx: Any) -> OnboardingContext:
    """Flag the context as "user requested custom mode"."""
    onboarding = OnboardingContext(mode="customizing")
    setattr(ctx, "onboarding", onboarding)
    return onboarding


# ---------------------------------------------------------------------------
# Internals


def _utterance_has_intent(utterance: str) -> bool:
    if not utterance:
        return False
    for token in _TOKEN_RE.findall(utterance):
        if token.lower() in WORKFLOW_INTENT_TOKENS:
            return True
    return False


def _format_recommendation_line(rec: TransferRecommendation) -> str:
    parts = [f"{rec.source_case} (similarity {rec.similarity:.2f}"]
    record = rec.source_calibration_record
    if record is not None and record.objective_name and record.objective_value is not None:
        try:
            value = float(record.objective_value)
        except (TypeError, ValueError):
            value = None
        if value is not None:
            parts[-1] += f", calibrated {record.objective_name}={value:.3f}"
    parts[-1] += ")"
    return "".join(parts)


__all__ = [
    "WORKFLOW_INTENT_TOKENS",
    "OnboardingDecision",
    "OnboardingContext",
    "is_new_case",
    "should_offer_transfer",
    "maybe_offer_onboarding",
    "format_onboarding_chat_block",
    "parse_onboarding_response",
    "apply_onboarding_acceptance",
    "mark_customize",
]
