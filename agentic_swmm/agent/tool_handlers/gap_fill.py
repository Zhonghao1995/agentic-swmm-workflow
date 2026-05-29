"""Gap-fill governance handlers (PRD #128 — Phase 2 Group C, FINAL group).

Family: HITL pause / L5 subjective judgement entry points.

The two handlers in this module are the explicit human-in-the-loop
seams the planner invokes when it must hand off to a modeller:

* ``_request_expert_review_tool`` — PRD-Z. A thin shim around the
  real handler in :mod:`agentic_swmm.hitl.request_expert_review`. The
  shim is the integration seam the registry calls through so the
  pause/prompt/record logic can evolve independently of tool wiring.
* ``_request_gap_judgement_tool`` — PRD-GF-L5. The subjective
  judgement entry point. The LLM invokes this when a hydrological
  choice has no single right answer (pour point, storm event,
  metric weighting, continuity tolerance). The handler routes
  through the enumerator + per-gap UI + recorder. L5 is human-only
  and cannot be auto-approved or registry-resolved.

The two L5 helpers ``_build_default_llm_provider`` and
``_is_tty_for_l5`` and the ledger re-stitch
``_restitch_l5_fields_in_ledger`` move with the handler — they are
only used here. ``_is_tty_for_l5`` is also a monkeypatch seam for
the L5 headless-block tests, so its public name and module path
(``agentic_swmm.agent.tool_registry._is_tty_for_l5``) must keep
working — see the compatibility re-export in ``tool_registry``.

``_failure`` comes from ``tool_handlers/_shared`` — the cross-cutting
helpers every family imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure
from agentic_swmm.agent.types import ToolCall


def _request_expert_review_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Thin shim around :func:`agentic_swmm.hitl.request_expert_review`.

    The real handler lives in the ``hitl`` package so the tool wiring
    and the pause/prompt/record logic can evolve independently. The
    shim is the integration seam the registry calls through.
    """
    from agentic_swmm.hitl.request_expert_review import request_expert_review

    return request_expert_review(call, session_dir)


# CONCURRENCY-OWNER: PRD-GF-L5
def _build_default_llm_provider() -> Any:
    """Construct the LLM provider used by the L5 enumerator.

    Late import so this file stays free of the provider import at module
    load. The provider seam matches what the planner uses
    (``respond_with_tools``).

    Construction is routed through ``make_provider`` so the L5
    enumerator honours ``provider.default`` (``openai`` by default, or
    ``anthropic``) instead of hard-coding a backend. The model is read
    from the matching ``<provider>.model`` config key; ``load_config``
    supplies the canonical per-provider defaults.
    """
    from agentic_swmm.config import DEFAULT_PROVIDER, load_config
    from agentic_swmm.providers.factory import make_provider

    config = load_config()
    provider_name = config.get("provider.default", DEFAULT_PROVIDER)
    model = config.get(f"{provider_name}.model")
    return make_provider(provider_name, model=model)


def _is_tty_for_l5() -> bool:
    """Return whether stdin+stdout are both TTYs (the L5 prompt seam).

    Pulled out as a helper so tests can monkeypatch it without faking
    sys.stdin.isatty across an entire process.
    """
    import sys as _sys

    return _sys.stdin.isatty() and _sys.stdout.isatty()


def _request_gap_judgement_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Handler for the ``request_gap_judgement`` ToolSpec (PRD-GF-L5).

    Routes the call through the enumerator + per-gap UI + recorder.
    The flow:

    1. Validate the three required arguments (``gap_kind``,
       ``context``, ``evidence_ref``). Missing args return
       ``{ok: false}`` with a clear summary — the planner can retry
       with corrected args.
    2. Ask the LLM to enumerate N candidates with tradeoffs cited
       via :func:`enumerate_candidates`. The call is recorded under
       ``09_audit/llm_calls.jsonl`` with ``caller=gap_fill.enumerator``.
    3. Show the per-gap pause UI via :func:`prompt_judgement`. The
       user picks one candidate and optionally supplies a free-form
       note. Non-TTY contexts raise :class:`JudgementBlocked` —
       judgement is never automated.
    4. Build an L5 :class:`GapDecision` and persist it via
       :func:`record_gap_decisions`. The decision carries
       ``resume_mode="llm_replan"`` and
       ``enumerator_llm_call_id`` for round-trip lookup.
    5. Return ``{ok, decision_id, resume_mode, gap_kind, summary}``
       so the planner's replan-injection branch can pull the decision
       into the next LLM turn.
    """
    # Import the *modules* (not the symbols) so test-side
    # ``mock.patch("agentic_swmm.gap_fill.llm_enumerator.enumerate_candidates", ...)``
    # rebinds the attribute the handler actually reads. Importing the
    # names directly would late-bind once into this function's locals
    # and ignore the patch.
    from agentic_swmm.gap_fill import (
        llm_enumerator as _llm_enumerator,
        ui_per_gap as _ui_per_gap,
    )
    from agentic_swmm.gap_fill.protocol import (
        GapDecision,
        ProposerInfo,
        new_decision_id,
        new_gap_id,
    )
    from agentic_swmm.gap_fill.recorder import record_gap_decisions

    EnumeratorParseError = _llm_enumerator.EnumeratorParseError
    JudgementBlocked = _ui_per_gap.JudgementBlocked
    JudgementDeferred = _ui_per_gap.JudgementDeferred

    gap_kind = str(call.args.get("gap_kind") or "").strip()
    evidence_ref = str(call.args.get("evidence_ref") or "").strip()
    raw_context = call.args.get("context")
    if not gap_kind:
        return _failure(call, "gap_kind is required")
    if not evidence_ref:
        return _failure(call, "evidence_ref is required")
    if not isinstance(raw_context, dict):
        return _failure(call, "context is required and must be an object")
    context = dict(raw_context)

    # Build the default provider lazily — the tests stub
    # ``enumerate_candidates`` before this attribute is even read, so
    # the OpenAI SDK never needs to be available in CI.
    try:
        provider = _build_default_llm_provider()
    except Exception:  # pragma: no cover - defensive: tests stub before this matters
        provider = None
    try:
        candidates, enumerator_call_id = _llm_enumerator.enumerate_candidates(
            gap_kind=gap_kind,
            context=context,
            evidence_ref=evidence_ref,
            n_candidates=3,
            llm_provider=provider,
            run_dir=session_dir,
        )
    except EnumeratorParseError as exc:
        return _failure(call, f"enumerator parse error: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        return _failure(call, f"enumerator failed: {exc}")

    # Dereference ``_is_tty_for_l5`` through ``tool_registry`` rather
    # than this module's local binding so the L5 headless-block tests'
    # ``mock.patch("agentic_swmm.agent.tool_registry._is_tty_for_l5", ...)``
    # still intercepts the TTY probe after the PRD #128 Group C move.
    # The compatibility shim in ``tool_registry`` re-exports the same
    # function object; resolving it from there at call time keeps the
    # historical monkeypatch seam stable.
    from agentic_swmm.agent import tool_registry as _tool_registry

    try:
        user_pick_id, user_note = _ui_per_gap.prompt_judgement(
            gap_kind=gap_kind,
            candidates=candidates,
            evidence_ref=evidence_ref,
            llm_call_id=enumerator_call_id,
            is_tty=_tool_registry._is_tty_for_l5(),
        )
    except JudgementDeferred as exc:
        return {
            "tool": call.name,
            "args": dict(call.args),
            "ok": False,
            "summary": f"judgement deferred: {exc}",
        }
    except JudgementBlocked as exc:
        # L5 always blocks in headless mode — surface a clean,
        # actionable failure so a CI run aborts with intent.
        return {
            "tool": call.name,
            "args": dict(call.args),
            "ok": False,
            "summary": (
                f"L5 judgement blocked: {exc}. L5 is human-only and cannot "
                "be auto-approved or registry-resolved."
            ),
        }

    from agentic_swmm.gap_fill.protocol import _now_utc_iso  # type: ignore

    decision_id = new_decision_id()
    gap_id = new_gap_id()
    decision = GapDecision(
        decision_id=decision_id,
        gap_id=gap_id,
        severity="L5",
        # ``field`` is the L1/L3 argument-name slot. For L5 the closest
        # analogue is the gap_kind, so we store it there too — gives the
        # provenance ledger something to grep on without inventing a new
        # field shape.
        field=gap_kind,
        # Synthetic proposer info: L5 has no machine-side proposer (the
        # enumerator presents options but never chooses). ``source="human"``
        # and ``confidence="HIGH"`` reflect "the human made the call".
        proposer=ProposerInfo(
            source="human",
            confidence="HIGH",
            llm_call_id=enumerator_call_id,
        ),
        proposed_value=None,
        final_value=user_pick_id,
        proposer_overridden=False,
        decided_by="human",
        decided_at=_now_utc_iso(),
        resume_mode="llm_replan",
        human_decisions_ref=None,
        gap_kind=gap_kind,
        candidates=tuple(candidates),
        user_pick=user_pick_id,
        user_note=user_note,
        enumerator_llm_call_id=enumerator_call_id,
    )

    try:
        # GF-CORE's recorder rebuilds the GapDecision when it
        # populates ``human_decisions_ref``, which drops the L5
        # extension fields. We capture the returned (enriched) record
        # so we can restore the L5 block in the ledger below. The
        # cross-link in ``experiment_provenance.json`` is still
        # correct — only the L5-specific keys in ``gap_decisions.json``
        # need re-stitching. This keeps GF-CORE's recorder untouched.
        enriched = record_gap_decisions(session_dir, [decision])
    except Exception as exc:  # pragma: no cover - defensive
        return _failure(call, f"recorder failed: {exc}")

    _restitch_l5_fields_in_ledger(
        session_dir=session_dir,
        decision_id=enriched[0].decision_id,
        human_decisions_ref=enriched[0].human_decisions_ref,
        l5_decision=decision,
    )

    return {
        "tool": call.name,
        "args": dict(call.args),
        "ok": True,
        "decision_id": decision_id,
        "resume_mode": "llm_replan",
        "gap_kind": gap_kind,
        "summary": (
            f"L5 judgement recorded for gap_kind={gap_kind!r}; "
            f"user_pick={user_pick_id!r}"
        ),
    }


# CONCURRENCY-OWNER: PRD-GF-L5
def _restitch_l5_fields_in_ledger(
    *,
    session_dir: Path,
    decision_id: str,
    human_decisions_ref: str | None,
    l5_decision: Any,
) -> None:
    """Re-write the L5-specific fields back into ``gap_decisions.json``.

    The GF-CORE recorder rebuilds the :class:`GapDecision` when it
    populates ``human_decisions_ref``, and the rebuild drops the L5
    extension fields (``gap_kind`` / ``candidates`` / ``user_pick`` /
    ``user_note`` / ``enumerator_llm_call_id``). We avoid touching
    GF-CORE's module by post-processing the ledger here: locate the
    record by ``decision_id`` and splice the L5 block back in. The
    write goes through tmp-file + ``os.replace`` for atomicity.
    """
    import json as _json
    import os as _os
    import tempfile as _tempfile

    ledger_path = Path(session_dir) / "09_audit" / "gap_decisions.json"
    if not ledger_path.is_file():
        return
    try:
        payload = _json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    decisions_list = payload.get("decisions")
    if not isinstance(decisions_list, list):
        return

    l5_block = {
        "gap_kind": l5_decision.gap_kind,
        "candidates": [c.to_dict() for c in l5_decision.candidates],
        "user_pick": l5_decision.user_pick,
        "user_note": l5_decision.user_note,
        "enumerator_llm_call_id": l5_decision.enumerator_llm_call_id,
    }
    found = False
    for entry in decisions_list:
        if isinstance(entry, dict) and entry.get("decision_id") == decision_id:
            entry.update(l5_block)
            # Preserve the recorder's human_decisions_ref — the
            # ``enriched`` decision already carries it, but if the
            # caller passed an override use that.
            if human_decisions_ref is not None:
                entry["human_decisions_ref"] = human_decisions_ref
            found = True
            break
    if not found:
        return

    # Atomic write — same pattern as the GF-CORE recorder.
    fd, tmp_name = _tempfile.mkstemp(
        prefix=ledger_path.name + ".",
        suffix=".tmp",
        dir=str(ledger_path.parent),
    )
    try:
        with _os.fdopen(fd, "w", encoding="utf-8") as handle:
            _json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            _os.fsync(handle.fileno())
        _os.replace(tmp_name, ledger_path)
    except Exception:
        try:
            _os.unlink(tmp_name)
        except OSError:
            pass
        raise


__all__ = [
    "_request_expert_review_tool",
    "_request_gap_judgement_tool",
    "_build_default_llm_provider",
    "_is_tty_for_l5",
    "_restitch_l5_fields_in_ledger",
]
