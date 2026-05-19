from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable

from agentic_swmm.agent.continuation_classifier import ExecutionPath, classify
from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.intent_map import looks_like_plot_request, looks_like_swmm_request, select_relevant_mcp_servers, select_relevant_skills
from agentic_swmm.agent.intent_disambiguator import PLOT_CONFLICT_SIGNALS, disambiguate
from agentic_swmm.agent.memory_context import MemoryContext, gather_memory_context
from agentic_swmm.agent.memory_informed_policy import (
    MemoryHITLRequired,
    PolicyDecision,
    decide_with_memory,
)
from agentic_swmm.agent.memory_trace import log_memory_decision
from agentic_swmm.agent.planner_introspection import should_introspect
from agentic_swmm.agent.prompts import openai_planner_prompt
from agentic_swmm.agent.reporting import write_event
from agentic_swmm.agent.tool_registry import AgentToolRegistry, compute_intent_signals
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.agent.ui import Spinner, SpinnerState
from agentic_swmm.agent.workflow_modes import WorkflowContext, get_mode
# PRD-04: re-export workflow-mode helpers under their legacy planner-module
# names so downstream callers (tests and intent-classifier migration
# parity) keep working without an import-path update.
from agentic_swmm.agent.workflow_modes._helpers import (  # noqa: F401
    _asks_for_plot_options,
    _default_rain_kind,
    _is_negated,
)
from agentic_swmm.agent.workflow_modes._helpers import (  # noqa: F401
    existing_run_plot_done_text as _existing_run_plot_done_text,
    extract_after_label as _extract_after_label,
    extract_example_inp_path as _extract_example_inp_path,
    extract_inp_path as _extract_inp_path,
    extract_plot_choice as _extract_plot_choice,
    extract_run_dir as _extract_run_dir,
    plot_choice_prompt as _plot_choice_prompt,
    plot_output_path as _plot_output_path,
    prepared_inp_done_text as _prepared_inp_done_text,
    workflow_route_args as _workflow_route_args,
)
from agentic_swmm.audit.llm_calls import extract_usage_tokens, record_llm_call
from agentic_swmm.providers.openai_api import OpenAIProvider


# Number of consecutive failures of the *same* tool name that the
# OpenAI agent loop tolerates before giving up. Three strikes guards
# against the LLM getting stuck in a retry loop on the same broken
# call while still leaving room for a typo + one retry + a final
# pivot. The loop logic in OpenAIPlanner.run depends on this constant
# being at least 1.
SAME_TOOL_RETRY_LIMIT = 3


@dataclass
class PlannerRun:
    ok: bool
    plan: list[ToolCall]
    results: list[dict[str, Any]]
    final_text: str


def rule_plan(goal: str) -> list[ToolCall]:
    text = goal.lower()
    calls: list[ToolCall] = []
    if any(word in text for word in ("doctor", "diagnose", "check setup", "runtime")):
        calls.append(ToolCall("doctor", {}))
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
    # Fall back to a token sniff. We deliberately import the policy's
    # token helper lazily so the import cycle stays shallow.
    from agentic_swmm.agent.memory_informed_policy import _utterance_tokens

    tokens = _utterance_tokens(goal)
    if len(tokens) == 1:
        return tokens[0]
    return None


class OpenAIPlanner:
    def __init__(
        self,
        provider: OpenAIProvider,
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

        # PRD_runtime: classifier-driven short-circuit. When the user's
        # prompt continues a prior SWMM run with a plot-style request
        # (and we already have an active_run_dir from the previous
        # turn), skip ``select_workflow_mode`` and the introspection
        # cluster — go straight to inspect_plot_options + plot_run.
        early_route = self._classify_plot_continuation(goal, prior_state)
        if early_route is not None and auto_router_enabled:
            return self._dispatch_workflow_mode(
                mode_name="existing_run_plot",
                goal=goal,
                session_dir=session_dir,
                plan=plan,
                route=early_route,
                executor=executor,
            )

        if _looks_like_swmm_request(goal) and auto_router_enabled:
            self._consult_workflow_skills(
                goal=goal,
                plan=plan,
                executor=executor,
                prior_session_state=prior_state,
            )
            route_args = _workflow_route_args(goal)
            # PRD-07 Phase 3: consult memory before the LLM
            # disambiguator. Empty memory yields a ``llm`` decision and
            # we fall through unchanged (paper-grade reproducibility
            # on fresh projects). A populated store can short-circuit
            # to ``auto_complete`` and skip the LLM call entirely, or
            # raise ``MemoryHITLRequired`` on high-stakes + zero
            # evidence — the runtime catches the exception and surfaces
            # the escalation prompt to the user.
            self._consult_memory_informed_policy(
                goal=goal,
                trace_path=trace_path,
                session_dir=session_dir,
                prior_session_state=prior_state,
            )
            # PRD #111: LLM disambiguation for compound plot-conflict
            # goals. Returns ``None`` for unambiguous prompts so the
            # deterministic SOP fast-path runs unchanged (paper-grade
            # reproducibility, user story 2). When it returns a mode
            # we inject it as ``mode=<picked>`` so the tool's
            # explicit-mode short-circuit fires.
            mode_hint = self._maybe_disambiguate(goal=goal, trace_path=trace_path, session_dir=session_dir)
            if mode_hint is not None:
                route_args = {**route_args, "mode": mode_hint}
            route_call = ToolCall("select_workflow_mode", route_args)
            plan.append(route_call)
            self.emit(f"[{len(plan)}] select_workflow_mode")
            route_result = executor.execute(route_call, index=len(plan))
            self.emit(f"OK: {route_result.get('summary') or 'completed'}")
            route = route_result.get("results") if isinstance(route_result.get("results"), dict) else {}
            if route.get("missing_inputs"):
                final_text = str(route.get("user_prompt") or "Please provide the missing SWMM workflow inputs.")
                return PlannerRun(ok=True, plan=plan, results=executor.results, final_text=final_text)
            # PRD-04: workflow-mode dispatch reads from the
            # ``agent/workflow_modes`` registry. Adding a mode means
            # adding one adapter file; the planner does not change.
            dispatched = self._dispatch_workflow_mode(
                mode_name=str(route.get("mode") or ""),
                goal=goal,
                session_dir=session_dir,
                plan=plan,
                route=route,
                executor=executor,
            )
            if dispatched is not None:
                return dispatched

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
                    tools=self.registry.schemas(),
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
                break

            outputs: list[dict[str, Any]] = []
            step_had_failure = False
            giveup_tool: str | None = None
            for provider_call in response.tool_calls:
                call = self.registry.validate(provider_call)
                plan.append(call)
                if self.verbose:
                    self.emit(f"[{len(plan)}] {call.name} {json.dumps(call.args, sort_keys=True)}")
                else:
                    self.emit(f"[{len(plan)}] {call.name}")
                result = executor.execute(call, index=len(plan))
                status = "OK" if result.get("ok") else "FAILED"
                self.emit(f"{status}: {result.get('summary') or 'completed'}")
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
                    # Track consecutive failures of the same tool name.
                    if last_failed_tool == call.name:
                        consecutive_failures += 1
                    else:
                        last_failed_tool = call.name
                        consecutive_failures = 1
                    if consecutive_failures >= SAME_TOOL_RETRY_LIMIT:
                        giveup_tool = call.name
                    # Stop running the rest of this step's tool batch —
                    # the failed tool's output likely changes context
                    # for siblings.
                    break
                # A successful tool resets the same-tool failure streak.
                last_failed_tool = None
                consecutive_failures = 0

            input_items = outputs

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

        return PlannerRun(ok=ok, plan=plan, results=executor.results, final_text=final_text)

    def _maybe_disambiguate(
        self,
        *,
        goal: str,
        trace_path: Path,
        session_dir: Path,
    ) -> str | None:
        """Return an LLM-picked workflow mode for compound plot-conflict goals.

        PRD #111. The trigger fires only when ``wants_plot`` and another
        action verb both fire — the small fraction of goals where the
        keyword fallback's priority is ambiguous. For everything else
        this short-circuits to ``None`` and the deterministic SOP
        fast-path runs unchanged.

        Records one ``intent_disambiguation`` trace event per call so
        the audit trail captures the goal, which signals fired, the
        picked mode, and whether we fell back. The provider call
        itself is funnelled through ``record_llm_call`` under
        ``model_role="disambiguate_intent"`` for symmetry with the
        rest of the LLM trace.
        """

        signals = compute_intent_signals(goal)
        if not signals.get("wants_plot"):
            return None
        if not any(signals.get(name) for name in PLOT_CONFLICT_SIGNALS):
            return None

        conflict_signals = [
            name for name in ("wants_plot", *PLOT_CONFLICT_SIGNALS) if signals.get(name)
        ]
        _start = time.monotonic()

        def _on_response(response: Any, prompt: tuple[Any, Any], call_duration_ms: int) -> None:
            # PRD-LLM-TRACE: every LLM API invocation funnels through
            # ``record_llm_call`` so ``09_audit/llm_calls.jsonl``
            # captures a symmetric trace. ``model_role`` lets a paper
            # reviewer grep for disambiguation interventions
            # specifically.
            _tokens_in, _tokens_out = extract_usage_tokens(response)
            record_llm_call(
                run_dir=session_dir,
                caller="planner",
                model_role="disambiguate_intent",
                prompt=prompt,
                response=response,
                tokens_in=_tokens_in,
                tokens_out=_tokens_out,
                duration_ms=call_duration_ms,
            )

        # ``disambiguate`` swallows provider exceptions and returns
        # ``None`` so the planner never crashes on LLM downtime
        # (user story 7).
        picked = disambiguate(
            goal=goal,
            signals=signals,
            provider=self.provider,
            on_response=_on_response,
        )
        duration_ms = int((time.monotonic() - _start) * 1000)
        write_event(
            trace_path,
            {
                "event": "intent_disambiguation",
                "goal": goal,
                "conflict_signals": conflict_signals,
                "picked_mode": picked,
                "duration_ms": duration_ms,
                "fallback_used": picked is None,
            },
        )
        return picked

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

        try:
            write_event(
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
        except Exception:  # pragma: no cover - audit must never break dispatch
            pass

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

        return decision

    def _classify_plot_continuation(
        self,
        goal: str,
        prior_session_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return a synthesized ``route`` dict when the prompt is a
        plot continuation against a known active_run_dir.

        Returns ``None`` when the classifier yields anything other than
        ``PLOT_CONTINUATION`` or there is no active_run_dir to plot.
        """
        active_run_dir = prior_session_state.get("active_run_dir")
        if not active_run_dir:
            # The prior state may nest workflow_state — be tolerant.
            workflow_state = prior_session_state.get("workflow_state")
            if isinstance(workflow_state, dict):
                active_run_dir = workflow_state.get("active_run_dir")
        if not active_run_dir:
            return None
        # Strip the synthetic "Previous run directory: ..." trailer
        # ``runtime_loop`` adds so that the classifier sees the user's
        # actual prompt vocabulary.
        prompt = goal.split("\n\nPrevious run directory:")[0].strip()
        path = classify(prompt, {"active_run_dir": active_run_dir})
        if path is not ExecutionPath.PLOT_CONTINUATION:
            return None
        return {
            "mode": "existing_run_plot",
            "provided_values": {"run_dir": str(active_run_dir)},
        }

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
        skill_names = _select_relevant_skills(goal)
        calls: list[ToolCall] = []
        if not skip_skills:
            calls.append(ToolCall("list_skills", {}))
            calls.extend(ToolCall("read_skill", {"skill_name": name}) for name in skill_names)
        if not skip_mcp:
            calls.append(ToolCall("list_mcp_servers", {}))
            calls.extend(
                ToolCall("list_mcp_tools", {"server": name, "timeout_seconds": 3})
                for name in _select_relevant_mcp_servers(skill_names)
            )
        for call in calls:
            plan.append(call)
            self.emit(f"[{len(plan)}] {call.name}")
            result = executor.execute(call, index=len(plan))
            status = "OK" if result.get("ok") else "FAILED"
            self.emit(f"{status}: {result.get('summary') or 'completed'}")

    def _dispatch_workflow_mode(
        self,
        *,
        mode_name: str,
        goal: str,
        session_dir: Path,
        plan: list[ToolCall],
        route: dict[str, Any],
        executor: AgentExecutor,
    ) -> PlannerRun | None:
        """Dispatch to a registered workflow-mode adapter, or ``None``.

        PRD-04. Replaces the hardcoded ``if route.get("mode") == "X"``
        chain. Returns ``None`` when ``mode_name`` is empty, unregistered,
        or registered as a spec-only stub (no ``run`` method) — in
        which case the planner falls through to its LLM main loop.
        """
        if not mode_name:
            return None
        adapter = get_mode(mode_name)
        if adapter is None or not hasattr(adapter, "run"):
            return None
        ctx = WorkflowContext(
            goal=goal,
            session_dir=session_dir,
            plan=plan,
            route=route,
            executor=executor,
            emit=self.emit,
        )
        return adapter.run(ctx)


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
