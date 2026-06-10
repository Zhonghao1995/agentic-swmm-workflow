"""New-case onboarding tool handler (flat-dispatch rewire).

Family: ``swmm-onboarding``.

Exposes ``apply_onboarding`` — the typed tool the planner calls after
the ``_consult_onboarding`` hook injects the onboarding chat block and
the user replies.  The hook surfaces the chat block in context; the
LLM relays it to the user verbatim; once the user replies the LLM
calls this tool with their natural-language response.

Handler flow
------------
1. Validate required args (``case_name``, ``response``).
2. Call :func:`parse_onboarding_response` to classify the response.
3. Dispatch on intent:

   * ``"accept"`` → :func:`apply_onboarding_acceptance` on a minimal
     surrogate ctx carrying the stored decision; extract the
     :class:`OnboardingContext`; collect ``memory_id`` from each
     applied recommendation.
   * ``"decline"`` → no parameter changes; surfaced clearly.
   * ``"customize"`` → :func:`mark_customize` flags the session for
     free-form editing; surfaced clearly.
   * ``"unknown"`` → safe default: treat as decline and tell the
     planner to ask again.

4. Return the house result-dict shape with a summary and
   ``applied_memory_ids`` so a subsequent run can stamp
   ``memories_applied``.

Fail-soft
---------
Any exception during application degrades to a result where
``ok=True`` (the tool ran) but ``applied_memory_ids`` is empty and
the summary explains that no parameters were applied.  The user
always gets some response, never a bare exception.

Public vocabulary
-----------------
No research terms appear in user-facing strings.  "Transferred
parameters from a similar watershed" is the canonical phrase.

``_failure`` comes from ``tool_handlers/_shared`` — the cross-cutting
helper every family imports.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure
from agentic_swmm.agent.types import ToolCall


# ---------------------------------------------------------------------------
# Resolve the parametric-store path the same way the planner hook does so
# both sides see the same durable state.
# ---------------------------------------------------------------------------

def _resolve_memory_dir() -> Path:
    override = os.environ.get("AISWMM_MEMORY_DIR")
    if override:
        return Path(override).expanduser().resolve()
    from agentic_swmm.utils.paths import repo_root
    return repo_root() / "memory" / "modeling-memory"


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

def _apply_onboarding_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Apply a user's reply to the new-case onboarding offer.

    Required args:
        case_name (str): The case the onboarding was offered for.
        response  (str): The user's natural-language reply
                         (Y / n / customize, or free text).
    """
    case_name = call.args.get("case_name")
    if not isinstance(case_name, str) or not case_name.strip():
        return _failure(call, "apply_onboarding: required arg 'case_name' missing or empty")
    case_name = case_name.strip()

    response_text = call.args.get("response")
    if not isinstance(response_text, str):
        return _failure(call, "apply_onboarding: required arg 'response' missing")

    from agentic_swmm.agent.onboarding import (
        OnboardingDecision,
        apply_onboarding_acceptance,
        mark_customize,
        maybe_offer_onboarding,
        parse_onboarding_response,
    )

    intent = parse_onboarding_response(response_text)

    # For "accept" or "customize" we need the recommendations so we can
    # report which memory ids were applied.  Re-run the recommender now.
    # This is lightweight (calibration-store read + similarity scores) and
    # deterministic — the same call the hook made, minus the parametric-
    # store guard because we are already post-gate.
    recommendations: list[Any] = []
    if intent in ("accept", "customize"):
        try:
            memory_dir = _resolve_memory_dir()
            parametric_store = memory_dir / "parametric_memory.jsonl"
            calibration_store = memory_dir / "calibration_memory.jsonl"
            negative_store = memory_dir / "negative_lessons.jsonl"
            storm_library = memory_dir / "storm_library.yaml"
            benchmarks = memory_dir / "reference_benchmarks.yaml"
            # Look for the target INP in conventional locations.
            from agentic_swmm.utils.paths import repo_root
            from agentic_swmm.memory.cross_watershed_transfer import (
                _candidate_inp_locations,
            )
            target_inp: Path | None = None
            for candidate in _candidate_inp_locations(case_name, repo_root()):
                if candidate.is_file():
                    target_inp = candidate
                    break

            decision = maybe_offer_onboarding(
                case_name=case_name,
                utterance="run",  # intent token — gate is already passed
                target_inp=target_inp,
                parametric_store=parametric_store,
                calibration_store=calibration_store,
                negative_lessons_store=negative_store,
                storm_library_path=storm_library,
                benchmarks_path=benchmarks,
                top_k=3,
            )
            recommendations = decision.recommendations
        except Exception:
            recommendations = []

    # --- dispatch on intent --------------------------------------------------

    if intent == "accept":
        applied_memory_ids: list[str] = []
        applied_case: str | None = None
        applied_params: dict[str, float] = {}
        try:
            if recommendations:
                # Build a minimal surrogate decision so apply_onboarding_acceptance
                # has the recommendation list it needs.
                from agentic_swmm.agent.onboarding import OnboardingDecision

                surrogate_decision = OnboardingDecision(
                    target_case=case_name,
                    triggered=True,
                    reason="new_case",
                    recommendations=recommendations,
                )
                ctx = _MinimalCtx()
                onboarding = apply_onboarding_acceptance(ctx, surrogate_decision)
                applied_params = dict(onboarding.defaults)
                applied_case = onboarding.accepted_source_case
                applied_memory_ids = [
                    rec.memory_id for rec in recommendations if rec.memory_id
                ]
        except Exception:
            pass

        if applied_case:
            summary = (
                f"Applied transferred parameters from a similar watershed "
                f"({applied_case}) to {case_name!r}. "
                f"Parameters: {_fmt_params(applied_params)}. "
                f"Source memory ids: {applied_memory_ids or '(none)'}."
            )
        else:
            summary = (
                f"Onboarding accepted for {case_name!r}. "
                "No similar-watershed parameters were available; "
                "the session will continue with default values."
            )
        return {
            "tool": call.name,
            "args": call.args,
            "ok": True,
            "intent": "accept",
            "case_name": case_name,
            "applied_memory_ids": applied_memory_ids,
            "applied_parameters": applied_params,
            "applied_source_case": applied_case,
            "summary": summary,
        }

    if intent == "decline":
        return {
            "tool": call.name,
            "args": call.args,
            "ok": True,
            "intent": "decline",
            "case_name": case_name,
            "applied_memory_ids": [],
            "applied_parameters": {},
            "applied_source_case": None,
            "summary": (
                f"Onboarding declined for {case_name!r}. "
                "Continuing with default parameters — "
                "you can request transferred parameters from a similar "
                "watershed at any time."
            ),
        }

    if intent == "customize":
        try:
            ctx = _MinimalCtx()
            mark_customize(ctx)
        except Exception:
            pass
        source_case = recommendations[0].source_case if recommendations else None
        custom_note = (
            f"Recommended source: {source_case}." if source_case else ""
        )
        return {
            "tool": call.name,
            "args": call.args,
            "ok": True,
            "intent": "customize",
            "case_name": case_name,
            "applied_memory_ids": [],
            "applied_parameters": {},
            "applied_source_case": None,
            "summary": (
                f"Onboarding set to custom mode for {case_name!r}. "
                f"{custom_note} "
                "Tell me which parameters you would like to adjust."
            ).strip(),
        }

    # intent == "unknown" — safe default: treat as decline, ask again
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "intent": "unknown",
        "case_name": case_name,
        "applied_memory_ids": [],
        "applied_parameters": {},
        "applied_source_case": None,
        "summary": (
            f"Reply not recognised for {case_name!r}. "
            "Please answer [Y / n / customize]: "
            "Y to use parameters transferred from a similar watershed, "
            "n to skip, or 'customize' to choose your own."
        ),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _MinimalCtx:
    """Minimal ctx surrogate that apply_onboarding_acceptance / mark_customize
    can attach an ``onboarding`` attribute to."""
    onboarding: Any = None


def _fmt_params(params: dict[str, float], *, max_items: int = 3) -> str:
    if not params:
        return "(none)"
    items = sorted(params.items())[:max_items]
    parts = []
    for k, v in items:
        try:
            parts.append(f"{k}={float(v):g}")
        except (TypeError, ValueError):
            parts.append(f"{k}={v}")
    suffix = f" (+{len(params) - max_items} more)" if len(params) > max_items else ""
    return ", ".join(parts) + suffix


__all__ = [
    "_apply_onboarding_tool",
]
