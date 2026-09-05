from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
import re

from pathlib import Path
from typing import IO, Any, Callable

from agentic_swmm.agent.digest_render import brief_result, render_step
from agentic_swmm.agent.executor import DENIED_SUMMARY, AgentExecutor
from agentic_swmm.agent.intent_classifier import looks_like_plot_request, looks_like_swmm_request, select_relevant_mcp_servers, select_relevant_skills
from agentic_swmm.agent.memory_context import MemoryContext, gather_memory_context, parametric_memory_block
from agentic_swmm.agent.memory_informed_policy import (
    MemoryHITLRequired,
    PolicyDecision,
    decide_with_memory,
)
from agentic_swmm.agent.memory_trace import log_memory_decision
from agentic_swmm.agent.planner_introspection import should_introspect
from agentic_swmm.agent.prompts import openai_planner_prompt
from agentic_swmm.agent.reporting import write_event
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.agent.ui import Spinner, SpinnerState
from agentic_swmm.audit.llm_calls import extract_usage_tokens, record_llm_call
from agentic_swmm.providers.base import ChatProvider


# Number of consecutive failures of the *same* tool name that the
# OpenAI agent loop tolerates before giving up. Three strikes guards
# against the LLM getting stuck in a retry loop on the same broken
# call while still leaving room for a typo + one retry + a final
# pivot. The loop logic in Planner.run depends on this constant
# being at least 1.
SAME_TOOL_RETRY_LIMIT = 3

# Issue #355: cumulative tool failures within one turn before the
# planner pauses for the user. Unlike ``SAME_TOOL_RETRY_LIMIT`` this
# budget survives pivots to a different tool AND interleaved
# successes — the reported session chained six failures across four
# tools without ever tripping the same-tool guard because successful
# ``list_dir``/``read_file`` probes kept resetting it.
PIVOT_CHECKPOINT_LIMIT = 3

# Same switch as agentic_swmm/hitl/request_expert_review.py: opting a
# headless run out of expert-review pauses also lets it continue past
# failure checkpoints. One autonomy knob, not two.
_HITL_AUTO_APPROVE_ENV = "AISWMM_HITL_AUTO_APPROVE"


def _stdin_is_interactive() -> bool:
    """TTY check behind a seam so tests can force either mode."""
    return sys.stdin.isatty()


def _hitl_auto_approve() -> bool:
    return os.environ.get(_HITL_AUTO_APPROVE_ENV, "").strip() in {"1", "true", "True", "yes"}


def _prompt_continue_past_failures(prompt: str) -> bool:
    """Fail-closed continue prompt: only an explicit yes proceeds."""
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _is_user_denial(result: dict[str, Any]) -> bool:
    """True for a tool result the user declined at the approval prompt."""
    permission = result.get("permission") if isinstance(result, dict) else None
    if isinstance(permission, dict) and permission.get("prompted") and not permission.get("approved", True):
        return True
    return str(result.get("summary") or "") == DENIED_SUMMARY


def _declined_final_text(call: ToolCall, result: dict[str, Any] | None = None) -> str:
    """The one-line report for a turn that ended at the approval prompt.

    A human said no, or nobody was there to say yes: live finding F-138
    (2026-09-04, S62 headless) showed the fail-closed refusal narrated as
    "You declined", which nobody did. The headless case names the cause
    and the opt-in instead.
    """
    parts = []
    for key, value in list(call.args.items()):
        text = str(value)
        if not text.strip() or text in ("[]", "{}", "None"):
            continue
        parts.append(f"{key}={text[:40]}{'...' if len(text) > 40 else ''}")
        if len(parts) == 3:
            break
    detail = f" ({', '.join(parts)})" if parts else ""
    hint = str((result or {}).get("hint") or "")
    if "No terminal was attached" in hint:
        return (
            f"No terminal was attached, so {call.name}{detail} was refused rather than run "
            "unattended; nothing ran this turn. Set AISWMM_AUTO_APPROVE=1 for trusted "
            "automation, or run the shell interactively."
        )
    return (
        f"You declined {call.name}{detail}, so nothing ran this turn. "
        "Ask again when ready, or say what to change (area, dates, route)."
    )


def _format_failure_inventory(failures: list[tuple[str, str]]) -> str:
    """Collapse (tool, summary) pairs into ``tool ×N (first summary)`` parts."""
    order: list[str] = []
    counts: dict[str, int] = {}
    first_summary: dict[str, str] = {}
    for tool, summary in failures:
        if tool not in counts:
            order.append(tool)
            counts[tool] = 0
            first_summary[tool] = (summary or "unspecified failure")[:100]
        counts[tool] += 1
    parts: list[str] = []
    for tool in order:
        label = f"{tool} ×{counts[tool]}" if counts[tool] > 1 else tool
        parts.append(f"{label} ({first_summary[tool]})")
    return "; ".join(parts)


@dataclass
class PlannerRun:
    ok: bool
    plan: list[ToolCall]
    results: list[dict[str, Any]]
    final_text: str


_INP_TOKEN_RE = re.compile(r"[\w./\\-]+\.inp\b", re.IGNORECASE)


def rule_plan(goal: str) -> list[ToolCall]:
    text = goal.lower()
    calls: list[ToolCall] = []
    if any(word in text for word in ("doctor", "diagnose", "check setup", "runtime")):
        calls.append(ToolCall("doctor", {}))
    # A goal that names an .inp and asks to run it is unambiguous: route
    # it to run_swmm_inp instead of the doctor fallback. The rule planner
    # stays deliberately minimal, but "run <model>.inp" is its bread and
    # butter (the LLM planner handles everything fuzzier). Bare filenames
    # resolve under examples/ via the same helper the runtime loop uses.
    inp_match = _INP_TOKEN_RE.search(goal)
    if inp_match and any(word in text for word in ("run", "execute", "运行", "跑")):
        from agentic_swmm.agent.single_shot import _find_repo_inp

        raw = inp_match.group(0)
        resolved = _find_repo_inp(raw)
        inp_path = str(resolved) if resolved is not None else raw
        calls.append(ToolCall("run_swmm_inp", {"inp_path": inp_path}))
    wants_acceptance = "acceptance" in text or "demo" in text
    wants_audit = "audit" in text
    wants_memory = "memory" in text or "summarize" in text
    wants_report = "report" in text or "summarize" in text
    if "capabilities" in text or "能力" in text:
        calls.append(ToolCall("capabilities", {}))
    if wants_acceptance:
        calls.append(ToolCall("demo_acceptance", {"run_id": "agent-latest", "keep_existing": False}))
        if wants_audit or "and audit" in text:
            calls.append(ToolCall("audit_run", {"run_dir": "runs/acceptance/agent-latest", "workflow_mode": "acceptance", "objective": goal}))
        if wants_memory:
            calls.append(ToolCall("summarize_memory", {"runs_dir": "runs/acceptance", "out_dir": "memory/modeling-memory"}))
        if wants_report or wants_audit:
            calls.append(ToolCall("read_file", {"path": "runs/acceptance/agent-latest/acceptance_report.md"}))
    if not calls:
        calls.append(ToolCall("doctor", {}))
    return calls


def _resolve_memory_dir_for_planner() -> Path:
    """Mirror ``audit_hook._resolve_memory_dir`` without importing it.

    The planner is the consumer; the audit hook is the writer.
    Importing the audit module would entangle two layers that have
    no other shared API, so the planner has its own tiny resolver
    that follows the same env var contract.
    """
    override = os.environ.get("AISWMM_MEMORY_DIR")
    if override:
        return Path(override)
    return Path("memory/modeling-memory")


_HIGH_STAKES_TOKENS: tuple[str, ...] = (
    # Verbs that mutate ``memory/`` or accept a calibration. The
    # list is short on purpose: the policy already escalates to
    # ``hitl`` only when *evidence* is zero, so a few false
    # positives here just gate an irreversible action behind an
    # extra confirm. False negatives are the real failure mode.
    "accept-calibration",
    "accept_calibration",
    "accept calibration",
    "promote-fact",
    "promote_fact",
    "promote fact",
    "reflect-apply",
    "reflect_apply",
    "reflect apply",
)


def _looks_high_stakes(goal: str) -> bool:
    """Return True when the goal text reads like a memory-mutating verb.

    Two passes: first the registry of memory verbs (PRD-06 Phase D.1)
    — a goal mentioning a verb the registry labels ``stakes="high"``
    is treated as high stakes without falling through to the keyword
    sniff. Then the legacy keyword sniff covers the older
    accept-calibration / promote-fact / reflect-apply verbs that
    predate the registry.
    """
    from agentic_swmm.agent.memory_verbs import list_verbs

    lowered = (goal or "").lower()
    for verb in list_verbs(mode="expert"):
        if verb.stakes == "high" and verb.name.lower() in lowered:
            return True
    return any(token in lowered for token in _HIGH_STAKES_TOKENS)


def _trace_event_best_effort(trace_path: Path, payload: dict[str, Any]) -> None:
    """Append one agent-trace event, swallowing every failure.

    The pre-LLM consult hooks emit audit-trail events that must never
    break dispatch — a read-only filesystem costs us the trace line,
    not the turn. The run loop's own ``write_event`` calls stay bare on
    purpose: mid-run trace integrity is part of that path's contract.
    Every future ``_consult_*`` hook should emit through this instead
    of copy-pasting the try/except envelope.
    """
    try:
        write_event(trace_path, payload)
    except Exception:  # pragma: no cover - audit must never break dispatch
        pass


def _resolve_case_name_for_memory(
    goal: str, prior_session_state: dict[str, Any]
) -> str | None:
    """Return the best-effort case anchor for memory consultation.

    Order of precedence:
        1. ``active_case_id`` carried over from the previous session
           (the most recently-touched case is usually the right one).
        2. ``recent_cases[0].case_id`` from prior state.
        3. Bare-token extraction from the goal that survives the
           policy's verb blocklist (a token like "saanich-b8" or
           "Todcreek"). We only return *one* candidate here — if the
           prompt mentions several names the policy's own match
           logic will refuse to auto-resolve and we fall to ``llm``.

    Returns ``None`` when no anchor can be derived. The policy still
    runs against an empty MemoryContext in that case so the audit
    log records the deferral.
    """
    if isinstance(prior_session_state, dict):
        candidate = prior_session_state.get("active_case_id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        recent = prior_session_state.get("recent_cases")
        if isinstance(recent, list) and recent:
            first = recent[0]
            if isinstance(first, dict):
                rid = first.get("case_id")
                if isinstance(rid, str) and rid.strip():
                    return rid.strip()
    # Live finding F-21 (2026-09-02): "Fetch a SWMM model ... for downtown
    # Victoria BC" tokenises to ten candidates, so the sniff below returned
    # None in every session and the parametric store was never consulted.
    # The run directory and provenance.case_name already carry the place
    # slug ("downtown-victoria-bc"); anchor memory on the same key.
    from agentic_swmm.agent.session_bootstrap import _extract_place_slug

    place = _extract_place_slug(goal)
    if place:
        return place
    # Fall back to a token sniff. We deliberately import the policy's
    # token helper lazily so the import cycle stays shallow.
    from agentic_swmm.agent.memory_informed_policy import _utterance_tokens

    tokens = _utterance_tokens(goal)
    if len(tokens) == 1:
        return tokens[0]
    return None


class Planner:
    def __init__(
        self,
        provider: ChatProvider,
        registry: AgentToolRegistry,
        *,
        max_steps: int,
        verbose: bool = False,
        emit: Callable[[str], None] | None = None,
        system_prompt_extras: list[str] | None = None,
        progress_stream: IO[str] | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.max_steps = max_steps
        self.verbose = verbose
        self.emit = emit or (lambda text: None)
        # PRD session-db-facts: ``runtime_loop`` injects per-session
        # extras here (``<project-facts>`` + ``<previous-session>``).
        # Empty list means no injection — keeps unit tests untouched.
        self.system_prompt_extras: list[str] = list(system_prompt_extras or [])
        # Issue #58 (UX-3): stream the "Thinking…" spinner here while
        # ``provider.respond_with_tools`` blocks on the LLM. Default to
        # ``sys.stdout`` so the runtime CLI gets a spinner with zero
        # extra wiring; tests can pass a captured stream.
        self._progress_stream: IO[str] = progress_stream if progress_stream is not None else sys.stdout

    # ------------------------------------------------------------------
    # Per-step output (PRD-185)
    # ------------------------------------------------------------------
    #
    # ``_emit_step`` is the single seam that renders one tool step to
    # the user. In ``verbose=True`` mode we keep the legacy two-line
    # shape (`[N] tool {args}` then `OK|FAILED: <summary>`) so the
    # debugging path is untouched. In digest mode (the new default)
    # we route through ``render_step`` which collapses the same
    # information onto a single line per the PRD table — plus an
    # auto-expanded ``Detail:`` block beneath every failure so the
    # operator never has to re-run with ``--verbose`` to see why a
    # tool failed.
    def _emit_step(
        self,
        *,
        index: int,
        call: ToolCall,
        result: dict[str, Any],
        executor: AgentExecutor,
    ) -> None:
        if self.verbose:
            self.emit(
                f"[{index}] {call.name} {json.dumps(call.args, sort_keys=True)}"
            )
            status = "OK" if result.get("ok") else "FAILED"
            self.emit(f"{status}: {result.get('summary') or 'completed'}")
            return
        # Digest path. Issue #193 item 2: read the permission decision
        # straight from the executor's result. The executor stamps
        # ``permission`` = ``{"prompted": bool, "approved": bool}`` on
        # every dispatch, so we no longer have to re-evaluate
        # ``auto_approve`` here or string-match the denial summary.
        # Fall back to a derived decision for stub executors used in
        # unit tests that pre-date this seam.
        is_read_only = self.registry.is_read_only(call.name)
        dry_run = bool(getattr(executor, "dry_run", False))
        perm = result.get("permission")
        if isinstance(perm, dict) and "prompted" in perm and "approved" in perm:
            prompted = bool(perm["prompted"])
            approved = bool(perm["approved"])
        else:
            profile = getattr(executor, "profile", None)
            if dry_run:
                auto_approved = True
            elif profile is not None and hasattr(profile, "auto_approve"):
                auto_approved = bool(profile.auto_approve(call.name, self.registry))
            else:
                # Stub executor (no profile) — treat read-only tools as
                # auto-approved (matches QUICK in real runs) and write
                # tools as prompted-and-approved.
                auto_approved = is_read_only
            prompted = (not dry_run) and (not auto_approved)
            summary = result.get("summary") or ""
            denied = prompted and summary == DENIED_SUMMARY
            approved = not denied
        ok = bool(result.get("ok"))
        brief = brief_result(call.name, result)
        # Digest cleanliness (user live test 2026-08-09, BUG-5 display
        # layer): successful read-only auto-approved steps are the
        # framework's own reconnaissance, not the user's workflow.
        # Printing 13 of them before the first real action buried the
        # process the user actually cares about. They stay in the trace
        # and in --verbose; the digest shows consequential steps and
        # every failure.
        if ok and is_read_only and not prompted:
            return
        error_detail: str | None = None
        if not ok:
            # PRD-185 / issue #193 item 1: try each known failure-detail
            # field in priority order. Subprocess-shaped tools populate
            # ``stderr_tail`` / ``stdout_tail`` today; the trailing
            # ``error`` / ``message`` / ``traceback`` keys are reserved
            # for handlers that adopt them in future (no handler in the
            # tree emits them at the moment, but the priority order is
            # pinned here so the digest UX is forward-compatible).
            # First non-empty string wins so the user always sees *some*
            # diagnostic beneath a failure row without ``--verbose``.
            for key in ("stderr_tail", "stdout_tail", "error", "message", "traceback"):
                candidate = result.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    # Trim any trailing blank lines so the expansion
                    # stays tight beneath the step row.
                    error_detail = candidate.rstrip("\n")
                    break
        self.emit(
            render_step(
                step=index,
                tool=call.name,
                is_read_only=is_read_only,
                prompted=prompted,
                approved=approved,
                ok=ok,
                brief=brief,
                error_detail=error_detail,
            )
        )

    def run(
        self,
        *,
        goal: str,
        session_dir: Path,
        trace_path: Path,
        executor: AgentExecutor,
        prior_session_state: dict[str, Any] | None = None,
    ) -> PlannerRun:
        """Run the OpenAI planner for one turn.

        ``prior_session_state`` is the previous turn's ``aiswmm_state.json``
        (or empty when there is none) and is consulted by
        ``should_introspect`` to deduplicate ``list_skills`` /
        ``list_mcp_servers`` / ``list_mcp_tools`` calls across turns.
        """
        plan: list[ToolCall] = []
        prior_state = prior_session_state if isinstance(prior_session_state, dict) else {}
        auto_router_enabled = os.environ.get("AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER") != "1"
        if os.environ.get("AISWMM_OPENAI_MOCK_TOOL_CALLS") and os.environ.get("AISWMM_FORCE_AUTO_WORKFLOW_ROUTER") != "1":
            auto_router_enabled = False

        # LLM-driven dispatch (post-refactor):
        # The defensive `select_workflow_mode` first-hop has been
        # removed. The LLM now reads SKILL.md descriptions and the
        # typed tool schemas directly and picks tools without a mode
        # gate — matching the OpenAI / Anthropic function-calling
        # contract. We still optionally prime LLM context with the
        # skill / MCP introspection cluster (``_consult_workflow_skills``)
        # and consult the memory-informed policy before the first
        # provider call so audit + escalation surfaces are unchanged.
        if _looks_like_swmm_request(goal) and auto_router_enabled:
            self._consult_workflow_skills(
                goal=goal,
                plan=plan,
                executor=executor,
                prior_session_state=prior_state,
            )
            # PRD-07 Phase 3: consult memory before the LLM picks a
            # tool. Empty memory yields a ``llm`` decision and we fall
            # through unchanged; a populated store can short-circuit
            # via ``MemoryHITLRequired`` on high-stakes + zero evidence
            # (the runtime catches the exception and surfaces the
            # escalation prompt to the user).
            self._consult_memory_informed_policy(
                goal=goal,
                trace_path=trace_path,
                session_dir=session_dir,
                prior_session_state=prior_state,
            )
            self._consult_onboarding(
                goal=goal,
                trace_path=trace_path,
                prior_session_state=prior_state,
            )

        tool_subset = self._tool_subset_for(goal, trace_path=trace_path)
        input_items: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"Goal: {goal}\n"
                    f"Session directory: {session_dir}\n"
                    "Use only the provided tools. Stop with a concise final answer after the evidence is sufficient. "
                    "For user-facing text, lead with the outcome and keep details to the key metrics, artifacts, evidence boundary, and next action."
                ),
            }
        ]
        previous_response_id: str | None = None
        final_text = ""
        ok = True
        # Same-tool retry guard: count consecutive failures of the same
        # tool name within this session so we can stop when the LLM is
        # clearly stuck. Limit lives at module scope as
        # ``SAME_TOOL_RETRY_LIMIT``.
        last_failed_tool: str | None = None
        consecutive_failures = 0
        # Session-level honesty (review P1-7): a tool failure that is never
        # recovered must not be washed into a clean success by a closing
        # natural-language turn. Set on any failed tool, cleared when a later
        # tool succeeds.
        unresolved_failure = False
        # Issue #355: cumulative failure ledger for this turn. Never reset
        # by pivots or interleaved successes (unlike the same-tool counters
        # above) — it powers the failure checkpoint and the honest
        # max_steps epitaph.
        failure_log: list[tuple[str, str]] = []
        failures_since_checkpoint = 0
        checkpoint_pending = False

        for step in range(1, self.max_steps + 1):
            # Issue #58 (UX-3): the LLM call is the longest silent
            # window in the loop (5-30s). Wrap it with a Thinking
            # spinner so the user sees motion. The spinner clears on
            # response (whether text or tool_calls) via ``finish()``.
            # CONCURRENCY-OWNER: PRD-LLM-TRACE
            # ``record_llm_call`` is the single observer for LLM API
            # invocations across the agent runtime. We measure wall
            # clock around the provider call, then funnel every
            # response through the observer so ``09_audit/`` gets one
            # JSONL line + one prompt dump per call.
            system_prompt_text = openai_planner_prompt(self.system_prompt_extras)
            with Spinner(
                "Thinking…",
                stream=self._progress_stream,
                state=SpinnerState.THINKING,
            ):
                _llm_call_start = time.monotonic()
                response = self.provider.respond_with_tools(
                    system_prompt=system_prompt_text,
                    input_items=input_items,
                    tools=self.registry.schemas(tool_subset),
                    previous_response_id=previous_response_id,
                )
                _llm_call_duration_ms = int((time.monotonic() - _llm_call_start) * 1000)
            _llm_tokens_in, _llm_tokens_out = extract_usage_tokens(response)
            record_llm_call(
                run_dir=session_dir,
                caller="planner",
                model_role="decide_next_tool",
                prompt=(system_prompt_text, input_items),
                response=response,
                tokens_in=_llm_tokens_in,
                tokens_out=_llm_tokens_out,
                duration_ms=_llm_call_duration_ms,
            )
            previous_response_id = response.response_id
            write_event(
                trace_path,
                {
                    "event": "planner_response",
                    "step": step,
                    "response_id": response.response_id,
                    "text": response.text,
                    "tool_calls": [{"call_id": call.call_id, "tool": call.name, "args": call.arguments} for call in response.tool_calls],
                },
            )
            if not response.tool_calls:
                final_text = response.text.strip()
                # A closing text turn does not clear an open tool failure.
                if unresolved_failure:
                    ok = False
                break

            if checkpoint_pending:
                # Issue #355 interactive path: the prompt waits for this
                # response so the user decides with the full picture —
                # the blockers so far plus the proposed pivot.
                proposed = [call.name for call in response.tool_calls]
                inventory = _format_failure_inventory(failure_log)
                print(
                    f"\n{len(failure_log)} tool failures this turn: {inventory}\n"
                    f"Planner proposes next: {', '.join(proposed)}",
                    file=self._progress_stream,
                )
                approved = _prompt_continue_past_failures("Continue past these failures? [y/N] ")
                write_event(
                    trace_path,
                    {
                        "event": "planner_failure_checkpoint",
                        "step": step,
                        "decision": "user_continue" if approved else "user_stop",
                        "failures": [{"tool": t, "summary": s} for t, s in failure_log],
                        "proposed": proposed,
                    },
                )
                if not approved:
                    ok = False
                    final_text = (
                        f"stopped by user at failure checkpoint after "
                        f"{len(failure_log)} tool failures: {inventory}"
                    )
                    break
                checkpoint_pending = False
                failures_since_checkpoint = 0

            outputs: list[dict[str, Any]] = []
            step_had_failure = False
            giveup_tool: str | None = None
            declined_call: ToolCall | None = None
            declined_result: dict[str, Any] | None = None
            for _call_index, provider_call in enumerate(response.tool_calls):
                call = self.registry.validate(provider_call)
                plan.append(call)
                result = executor.execute(call, index=len(plan))
                self._emit_step(
                    index=len(plan),
                    call=call,
                    result=result,
                    executor=executor,
                )
                # PRD-Y: ``skill_selected`` trace event. Sits between
                # ``session_start`` and the first concrete ``tool_call``
                # so audit notes can show which skill the agent
                # committed to before any deterministic-SWMM tool ran.
                if call.name == "select_skill" and result.get("ok"):
                    write_event(
                        trace_path,
                        {
                            "event": "skill_selected",
                            "skill_name": str(result.get("skill_name") or ""),
                            "tool_count": len(result.get("tools") or []),
                        },
                    )
                    # Live finding F-50 (2026-09-02): the goal-scoped
                    # schema subset (F-44) was computed once from the
                    # goal's keywords, so a skill the keywords missed
                    # stayed unreachable for the whole turn. Naming a
                    # skill is the model's way of asking for its tools;
                    # the next LLM call carries them.
                    tool_subset = self._grow_tool_subset(
                        tool_subset, str(result.get("skill_name") or ""), trace_path=trace_path
                    )
                outputs.append({"type": "function_call_output", "call_id": provider_call.call_id, "output": json.dumps(self.registry.output_for_model(result), sort_keys=True)})
                # CONCURRENCY-OWNER: PRD-GF-L5
                # L5 subjective-judgement replan injection. When the
                # ``request_gap_judgement`` tool resolves with
                # ``resume_mode="llm_replan"`` we fetch the recorded
                # decision (gap_kind, user_pick + summary, user_note)
                # from ``09_audit/gap_decisions.json`` and inject a
                # structured user_clarification message into the next
                # turn's input_items. The planner does not retry the
                # same tool — the LLM re-plans with the judgement in
                # context. See PRD-GF-L5 "Resume mode: llm_replan".
                if result.get("ok") and result.get("resume_mode") == "llm_replan":
                    _user_clarification = _build_l5_replan_clarification(
                        session_dir=session_dir,
                        decision_id=str(result.get("decision_id") or ""),
                    )
                    if _user_clarification is not None:
                        outputs.append(_user_clarification)
                if not result.get("ok"):
                    step_had_failure = True
                    unresolved_failure = True
                    failure_log.append((call.name, str(result.get("summary") or "")))
                    if _is_user_denial(result):
                        declined_call = call
                        declined_result = result
                    failures_since_checkpoint += 1
                    # Track consecutive failures of the same tool name.
                    if last_failed_tool == call.name:
                        consecutive_failures += 1
                    else:
                        last_failed_tool = call.name
                        consecutive_failures = 1
                    if consecutive_failures >= SAME_TOOL_RETRY_LIMIT:
                        giveup_tool = call.name
                    # Stop running the rest of this step's tool batch — the
                    # failed tool's output likely changes context for siblings.
                    # But every tool call the model emitted must still get
                    # exactly one output, or the next provider turn is a
                    # protocol error (review P1-8). Emit a skipped result for
                    # each sibling we are not executing.
                    for skipped in response.tool_calls[_call_index + 1:]:
                        outputs.append({
                            "type": "function_call_output",
                            "call_id": skipped.call_id,
                            "output": json.dumps(
                                {"ok": False, "skipped": True, "summary": "skipped: an earlier tool in this batch failed"},
                                sort_keys=True,
                            ),
                        })
                    break
                # A successful tool resets the same-tool failure streak and
                # clears the open-failure flag: the model recovered.
                last_failed_tool = None
                consecutive_failures = 0
                unresolved_failure = False

            input_items = outputs

            if declined_call is not None:
                # Live finding F-127 (2026-09-03, S58): after one "n" the
                # planner asked for the same fetch twice more with other
                # areas. A decline is the user's decision, not a failure to
                # route around: the turn ends here, and the shell reports
                # it as declined (exit code 3, F-63).
                ok = False
                final_text = _declined_final_text(declined_call, declined_result)
                write_event(
                    trace_path,
                    {"event": "planner_declined", "step": step, "tool": declined_call.name, "args": declined_call.args},
                )
                break

            if giveup_tool is not None:
                ok = False
                final_text = f"giving up: {giveup_tool} failed {SAME_TOOL_RETRY_LIMIT}× in a row"
                write_event(
                    trace_path,
                    {
                        "event": "planner_giveup",
                        "step": step,
                        "tool": giveup_tool,
                        "consecutive_failures": consecutive_failures,
                    },
                )
                break

            # Issue #355: cumulative-failure checkpoint. Runs after the
            # same-tool guard so the stricter, older contract keeps
            # precedence when both trip on the same step.
            if failures_since_checkpoint >= PIVOT_CHECKPOINT_LIMIT:
                if _stdin_is_interactive():
                    # Defer the question one response so the prompt can
                    # show the proposed pivot alongside the blockers.
                    checkpoint_pending = True
                elif _hitl_auto_approve():
                    write_event(
                        trace_path,
                        {
                            "event": "planner_failure_checkpoint",
                            "step": step,
                            "decision": "auto_approve",
                            "failures": [{"tool": t, "summary": s} for t, s in failure_log],
                        },
                    )
                    failures_since_checkpoint = 0
                else:
                    # Headless without the switch: stop right here — no
                    # point spending another LLM call on a turn nobody
                    # can approve (mirrors request_expert_review's
                    # fail-closed contract).
                    inventory = _format_failure_inventory(failure_log)
                    ok = False
                    final_text = (
                        f"stopped at failure checkpoint after {len(failure_log)} tool "
                        f"failures (non-interactive): {inventory}. Set "
                        f"{_HITL_AUTO_APPROVE_ENV}=1 to continue past failure "
                        "checkpoints in headless runs."
                    )
                    write_event(
                        trace_path,
                        {
                            "event": "planner_failure_checkpoint",
                            "step": step,
                            "decision": "headless_stop",
                            "failures": [{"tool": t, "summary": s} for t, s in failure_log],
                        },
                    )
                    break

            if executor.dry_run:
                # Existing short-circuit: dry-run produces no further
                # tool evidence, so a second LLM turn is pointless.
                if step_had_failure:
                    ok = False
                break
            # NOTE: we deliberately do NOT break on step_had_failure
            # here. The failed tool's output is already packed into
            # ``outputs`` (which becomes the next step's
            # ``input_items``) so the LLM gets a chance to retry,
            # pivot, or report the failure in natural language.
        else:
            ok = False
            final_text = f"planner stopped after max_steps={self.max_steps}"
            # Issue #355: the exhaustion epitaph must name what blocked
            # the turn, not just the step counter. The give-up path
            # writes ``planner_giveup``; this path gets its own event so
            # an auditor can tell a bounded loop from a starved one.
            if failure_log:
                final_text += (
                    f"; unresolved blockers ({len(failure_log)}): "
                    f"{_format_failure_inventory(failure_log)}"
                )
            write_event(
                trace_path,
                {
                    "event": "planner_max_steps_exhausted",
                    "step": self.max_steps,
                    "failures": [{"tool": t, "summary": s} for t, s in failure_log],
                },
            )

        return PlannerRun(ok=ok, plan=plan, results=executor.results, final_text=final_text)

    def _consult_memory_informed_policy(
        self,
        *,
        goal: str,
        trace_path: Path,
        session_dir: Path,
        prior_session_state: dict[str, Any],
    ) -> PolicyDecision | None:
        """Run the Phase 3 memory-informed disambiguation policy.

        The hook is **additive** — when memory is empty or the case
        cannot be resolved from the goal/state, the policy returns
        ``confidence="llm"`` and we fall through to existing behaviour
        unchanged. The hook never crashes the planner: any I/O or
        store-shape exception is swallowed so a corrupt memory dir
        cannot block dispatch.

        Side effects:
            * On every successful decision (including ``llm``) a
              :func:`log_memory_decision` line lands in
              ``<session_dir>/memory_trace.jsonl``.
            * On ``confidence="hitl"`` the hook raises
              :class:`MemoryHITLRequired` so the runtime can surface
              the blocking escalation prompt.

        Stakes detection is intentionally simple here: any goal whose
        text suggests calibration-accept or memory mutation is
        treated as ``high``. The policy itself handles the matrix of
        evidence vs. stakes; the planner just classifies the verb.
        """
        case_name = _resolve_case_name_for_memory(goal, prior_session_state)
        if not case_name:
            # Without a case-name anchor we cannot consult the
            # parametric store meaningfully. The Phase 3 policy still
            # runs against an empty MemoryContext so the audit trail
            # records *that* we consulted memory and decided to defer.
            context: MemoryContext = MemoryContext()
        else:
            try:
                memory_dir = _resolve_memory_dir_for_planner()
                context = gather_memory_context(
                    memory_dir=memory_dir,
                    case_name=case_name,
                )
            except Exception:  # pragma: no cover - defensive: memory must never break dispatch
                context = MemoryContext()

        stakes = "high" if _looks_high_stakes(goal) else "low"

        try:
            decision = decide_with_memory(goal, context, stakes=stakes)
        except Exception:  # pragma: no cover - defensive: policy is pure-function but stay safe
            return None

        # Best-effort transparency log. A failed log call must not
        # abort planning; the agent_trace.jsonl event below is a
        # separate, also-best-effort record.
        try:
            log_memory_decision(
                run_dir=session_dir,
                decision_point="planner_intent_disambiguation",
                context=context,
                decision=decision.resolved_case or "(none)",
                confidence=decision.confidence,
            )
        except Exception:  # pragma: no cover - audit must never break dispatch
            pass

        _trace_event_best_effort(
            trace_path,
            {
                "event": "memory_informed_policy",
                "goal": goal,
                "confidence": decision.confidence,
                "resolved_case": decision.resolved_case,
                "candidate_count": len(decision.candidates),
                "stakes": stakes,
                "reasoning": decision.reasoning,
            },
        )

        if decision.confidence == "hitl":
            raise MemoryHITLRequired(
                decision.escalation
                or "Memory-informed policy requires human confirmation.",
                memory_context=context,
                proposed_action=(
                    f"dispatch goal {goal!r} (stakes={stakes})"
                ),
                decision_point="planner_intent_disambiguation",
            )

        # Live finding F-39 (2026-09-02): the decision above was only ever
        # logged. Two sessions, one with a parametric hit and one without,
        # made identical tool calls because nothing put the prior runs in
        # front of the planner. Now the hits ride into the system prompt.
        block = parametric_memory_block(context)
        if block:
            self.system_prompt_extras.append(block)
            _trace_event_best_effort(
                trace_path,
                {
                    "event": "memory_context_injected",
                    "case_name": decision.resolved_case or case_name,
                    "prior_runs": context.parametric_hit_count,
                },
            )

        return decision

    def _consult_onboarding(
        self,
        *,
        goal: str,
        trace_path: Path,
        prior_session_state: dict[str, Any],
    ) -> None:
        """Surface a new-case onboarding offer before the first LLM call.

        Mirrors :meth:`_consult_memory_informed_policy` in placement and
        discipline:

        * **Gating**: only fires when the goal resolves to a case name AND
          ``is_new_case(case_name, ...)`` returns True.  Known case or
          unresolvable case → strict no-op, zero added latency beyond the
          ``is_new_case`` check.
        * **Output**: when the gate fires, appends the formatted onboarding
          chat block to ``self.system_prompt_extras`` so the LLM relays it
          verbatim to the user, and emits an ``onboarding_offer`` trace
          event.
        * **Fail-soft**: any I/O or store-shape exception is swallowed so a
          corrupt memory directory cannot block dispatch.
        * **No LLM calls**: the hook is deterministic (parametric-store
          read + similarity scoring).  It never calls the provider.

        The injected chat block includes a one-line instruction telling the
        planner to call ``apply_onboarding`` once the user replies, so the
        tool advertisement is in-context at the moment it is relevant.
        """
        case_name = _resolve_case_name_for_memory(goal, prior_session_state)
        if not case_name:
            return

        try:
            memory_dir = _resolve_memory_dir_for_planner()
            parametric_store = memory_dir / "parametric_memory.jsonl"

            from agentic_swmm.agent.onboarding import is_new_case

            if not is_new_case(case_name, parametric_store=parametric_store):
                return

            calibration_store = memory_dir / "calibration_memory.jsonl"
            negative_store = memory_dir / "negative_lessons.jsonl"
            storm_library = memory_dir / "storm_library.yaml"
            benchmarks = memory_dir / "reference_benchmarks.yaml"

            # Locate the target INP using the same conventions as the old
            # adapter hook.
            from agentic_swmm.utils.paths import repo_root as _repo_root
            from agentic_swmm.memory.cross_watershed_transfer import (
                _candidate_inp_locations,
            )
            target_inp = None
            for candidate in _candidate_inp_locations(case_name, _repo_root()):
                if candidate.is_file():
                    target_inp = candidate
                    break

            from agentic_swmm.agent.onboarding import maybe_offer_onboarding

            decision = maybe_offer_onboarding(
                case_name=case_name,
                utterance=goal,
                target_inp=target_inp,
                parametric_store=parametric_store,
                calibration_store=calibration_store,
                negative_lessons_store=negative_store,
                storm_library_path=storm_library,
                benchmarks_path=benchmarks,
                top_k=3,
            )
        except Exception:  # pragma: no cover - defensive: onboarding must never break dispatch
            return

        if not decision.triggered or not decision.chat_block:
            return

        # Append the chat block + tool advertisement to the system-prompt
        # extras so the LLM sees it before the first tool call.
        tool_hint = (
            "Once the user answers this onboarding prompt, call the "
            "apply_onboarding tool with case_name and their response."
        )
        block_with_hint = decision.chat_block + "\n\n" + tool_hint
        self.system_prompt_extras.append(
            "<onboarding_offer>\n" + block_with_hint + "\n</onboarding_offer>"
        )

        _trace_event_best_effort(
            trace_path,
            {
                "event": "onboarding_offer",
                "case_name": decision.target_case,
                "triggered": decision.triggered,
                "reason": decision.reason,
                "recommendation_count": len(decision.recommendations),
                "memory_ids": [
                    rec.memory_id
                    for rec in decision.recommendations
                    if getattr(rec, "memory_id", None)
                ],
            },
        )

    def _grow_tool_subset(
        self, tool_subset: "set[str] | None", skill_name: str, *, trace_path: Path | None = None
    ) -> "set[str] | None":
        """Add ``skill_name``'s tools to an active subset; ``None`` stays ``None``."""
        if tool_subset is None or not skill_name:
            return tool_subset
        from agentic_swmm.agent.skill_router import SkillRouter

        try:
            names = set(SkillRouter(self.registry).tools_for(skill_name).tool_names())
        except KeyError:
            return tool_subset
        added = sorted(names - tool_subset)
        if not added:
            return tool_subset
        grown = set(tool_subset) | names
        _trace_event_best_effort(
            trace_path,
            {"event": "tool_subset_grown", "skill": skill_name, "added": added, "tool_count": len(grown)},
        )
        return grown

    def _tool_subset_for(self, goal: str, *, trace_path: Path | None = None) -> "set[str] | None":
        """The tool names this goal's skills need, or ``None`` for all.

        Live finding F-44 (2026-09-02): the 57 tool schemas (64k
        characters) went out on every LLM call and were most of the 150k
        to 210k input tokens of a Canada chain turn. The schemas
        sent are the agent-internal bucket plus the tools of the skills
        ``select_relevant_skills`` picks for the goal; unknown skills are
        skipped, and a skill the model names through select_skill is added
        mid-turn (F-50). On by default since 2026-09-02: the live campaign
        measured -23% to -30% input tokens per turn with the same tool
        sequences across the Canada chain, calibration, climate, network QA
        and uncertainty scenarios. AISWMM_TOOL_SUBSET=0 sends every schema.
        """
        if os.environ.get(TOOL_SUBSET_ENV, "1").strip().lower() in ("0", "false", "no", "off"):
            return None
        from agentic_swmm.agent.skill_router import AGENT_INTERNAL_SKILL, SkillRouter

        router = SkillRouter(self.registry)
        skills = [AGENT_INTERNAL_SKILL, *_select_relevant_skills(goal)]
        names: set[str] = set()
        used: list[str] = []
        for skill in skills:
            try:
                names.update(router.tools_for(skill).tool_names())
            except KeyError:
                continue
            used.append(skill)
        if not names:
            return None
        _trace_event_best_effort(
            trace_path,
            {"event": "tool_subset", "skills": used, "tool_count": len(names), "total_tools": len(self.registry.names)},
        )
        return names

    def _consult_workflow_skills(
        self,
        *,
        goal: str,
        plan: list[ToolCall],
        executor: AgentExecutor,
        prior_session_state: dict[str, Any] | None = None,
    ) -> None:
        # PRD_runtime: skip introspection calls that the prior session
        # already made. Skipping ``list_skills`` automatically skips
        # the per-skill ``read_skill`` follow-ups (they only exist to
        # populate planner context that the prior turn already gathered).
        skip_skills, skip_mcp = should_introspect(prior_session_state or {}, goal)
        # Live finding F-70 (2026-09-02): the shell builds a fresh registry
        # every turn and an answer-continuation carries no prior state, so
        # the catalogue prologue (list_skills, list_mcp_servers, one
        # list_mcp_tools per relevant server: about ten tool calls) ran again
        # on the second modeling turn of a session although nothing in the
        # catalogue can change inside one process. Remember, per process,
        # the catalogue fingerprint that was last listed.
        fingerprint = _catalogue_fingerprint()
        if not _always_introspect() and _CATALOGUE_LISTED.get("fingerprint") == fingerprint:
            if not (skip_skills and skip_mcp):
                _trace_event_best_effort(
                    getattr(executor, "trace_path", None),
                    {"event": "prologue_skipped", "reason": "catalogue already listed in this process"},
                )
            skip_skills = skip_mcp = True
        skill_names = _select_relevant_skills(goal)
        calls: list[ToolCall] = []
        if not skip_skills:
            calls.append(ToolCall("list_skills", {}))
            # Live finding F-44 (2026-09-02): the results of these primed
            # calls were never sent to the model. ``run`` builds its
            # input_items from the goal alone, so seven SKILL.md reads per
            # session (67k chars) were executed, traced and discarded; #447's
            # cap changed the token count by nothing. The reads are off by
            # default now; AISWMM_PRIME_SKILL_READS restores them for anyone
            # who wants the old trace shape. list_skills stays (it is what
            # should_introspect keys on) and the MCP listing below warms the
            # node servers, which is a real effect.
            calls.extend(
                ToolCall("read_skill", {"skill_name": name})
                for name in skill_names[: _skill_priming_limit(len(skill_names))]
            )
        if not skip_mcp:
            calls.append(ToolCall("list_mcp_servers", {}))
            calls.extend(
                ToolCall("list_mcp_tools", {"server": name, "timeout_seconds": 10})
                for name in _select_relevant_mcp_servers(skill_names)
            )
        if calls and not _always_introspect():
            _CATALOGUE_LISTED["fingerprint"] = fingerprint
        for call in calls:
            plan.append(call)
            result = executor.execute(call, index=len(plan))
            self._emit_step(
                index=len(plan),
                call=call,
                result=result,
                executor=executor,
            )


#: How many relevant skills get their SKILL.md primed into the conversation
#: before the first LLM call. ``AISWMM_PRIME_SKILL_READS=all`` restores the
#: previous behaviour; any integer sets the cap.
PRIME_SKILL_READS_ENV = "AISWMM_PRIME_SKILL_READS"
#: Send only the goal's skills' tool schemas to the model (experiment, F-44).

#: Live finding F-70 (2026-09-02): per-process memo of the catalogue the
#: prologue last listed. Keyed on the registries' mtimes so an edited
#: skills.json or mcp.json lists again; AISWMM_ALWAYS_INTROSPECT=1 disables
#: the memo (the test suite sets it so every turn stays observable).
ALWAYS_INTROSPECT_ENV = "AISWMM_ALWAYS_INTROSPECT"
_CATALOGUE_LISTED: dict[str, Any] = {}


def _always_introspect() -> bool:
    return os.environ.get(ALWAYS_INTROSPECT_ENV, "").strip().lower() in ("1", "true", "yes")


def _catalogue_fingerprint() -> tuple[Any, ...]:
    from agentic_swmm.config import config_dir

    parts: list[Any] = [os.getpid()]
    for name in ("skills.json", "mcp.json"):
        try:
            parts.append(round((config_dir() / name).stat().st_mtime, 3))
        except OSError:
            parts.append(None)
    return tuple(parts)


def reset_catalogue_memo() -> None:
    """Forget the listed catalogue (tests, or after editing the registries)."""
    _CATALOGUE_LISTED.clear()

TOOL_SUBSET_ENV = "AISWMM_TOOL_SUBSET"
DEFAULT_PRIME_SKILL_READS = 0


def _skill_priming_limit(available: int) -> int:
    raw = os.environ.get(PRIME_SKILL_READS_ENV, "").strip().lower()
    if raw == "all":
        return available
    try:
        limit = int(raw) if raw else DEFAULT_PRIME_SKILL_READS
    except ValueError:
        limit = DEFAULT_PRIME_SKILL_READS
    return max(0, min(limit, available))


def _looks_like_swmm_request(goal: str) -> bool:
    return looks_like_swmm_request(goal)


def _looks_like_plot_request(goal: str) -> bool:
    return looks_like_plot_request(goal)


def _select_relevant_skills(goal: str) -> list[str]:
    return select_relevant_skills(goal)


def _select_relevant_mcp_servers(skill_names: list[str]) -> list[str]:
    return select_relevant_mcp_servers(skill_names)


# CONCURRENCY-OWNER: PRD-GF-L5
def _build_l5_replan_clarification(
    *,
    session_dir: Path,
    decision_id: str,
) -> dict[str, Any] | None:
    """Build the user_clarification message for an L5 replan turn.

    Returns a ``{"role": "user", "content": <text>}`` dict shaped for
    the next ``respond_with_tools`` ``input_items`` list. The content
    follows the format from PRD-GF-L5::

        [gap_decision]
        gap_kind: <kind>
        user_pick: <id> (<summary>)
        user_note: "<free-form text>"
        resume: re-plan from here. ...

    Returns ``None`` when the decision cannot be loaded — we silently
    skip injection rather than crashing the planner loop, since the
    function_call_output already carries enough information for the
    LLM to react.
    """
    # Late import keeps the planner module free of a gap-fill dep at
    # the top of the file — the injection is a leaf concern.
    from agentic_swmm.gap_fill.recorder import read_gap_decisions

    if not decision_id:
        return None
    try:
        decisions = read_gap_decisions(session_dir)
    except Exception:  # pragma: no cover - defensive
        return None
    match = next(
        (d for d in decisions if d.decision_id == decision_id and d.severity == "L5"),
        None,
    )
    if match is None:
        return None

    pick_summary = ""
    for cand in match.candidates:
        if cand.id == match.user_pick:
            pick_summary = cand.summary
            break
    user_pick_line = (
        f"user_pick: {match.user_pick} ({pick_summary})"
        if pick_summary
        else f"user_pick: {match.user_pick}"
    )
    note_line = (
        f'user_note: "{match.user_note}"' if match.user_note else "user_note: (none)"
    )
    content = (
        "[gap_decision]\n"
        f"gap_kind: {match.gap_kind}\n"
        f"{user_pick_line}\n"
        f"{note_line}\n"
        "resume: re-plan from here. The human has resolved the subjective "
        "judgement above; decide the next step in context of this choice."
    )
    return {"role": "user", "content": content}


# Back-compat alias: the class predates the two-provider factory and was
# published as ``OpenAIPlanner``; external callers may still import that
# name. Internal code and tests use ``Planner``.
OpenAIPlanner = Planner
