from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.hitl_surface import format_hitl_prompt
from agentic_swmm.agent.memory_context import MemoryContext
from agentic_swmm.agent.memory_informed_policy import MemoryHITLRequired
from agentic_swmm.agent.planner import OpenAIPlanner, PlannerRun, rule_plan
from agentic_swmm.agent.reporting import write_event
from agentic_swmm.agent.state import write_session_state
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


def call_payload(call: ToolCall) -> dict:
    return {"tool": call.name, "args": call.args}


def run_rule_plan(*, goal: str, registry: AgentToolRegistry, executor: AgentExecutor, max_steps: int, trace_path: Path) -> PlannerRun:
    plan = rule_plan(goal)
    if len(plan) > max_steps:
        plan = plan[:max_steps]
    write_event(trace_path, {"event": "session_start", "goal": goal, "session_dir": str(executor.session_dir), "plan": [call_payload(call) for call in plan]})
    ok = True
    try:
        for index, call in enumerate(plan, start=1):
            result = executor.execute(call, index=index)
            if not result.get("ok"):
                ok = False
                break
    finally:
        executor.close()
    return PlannerRun(ok=ok, plan=plan, results=executor.results, final_text="")


def run_openai_plan(
    *,
    goal: str,
    model: str,
    provider,
    registry: AgentToolRegistry,
    executor: AgentExecutor,
    max_steps: int,
    trace_path: Path,
    verbose: bool,
    emit,
    prior_session_state: dict | None = None,
    system_prompt_extras: list[str] | None = None,
) -> PlannerRun:
    write_event(
        trace_path,
        {
            "event": "session_start",
            "goal": goal,
            "session_dir": str(executor.session_dir),
            "planner": "openai",
            "model": model,
            "allowed_tools": registry.sorted_names(),
        },
    )
    planner = OpenAIPlanner(
        provider,
        registry,
        max_steps=max_steps,
        verbose=verbose,
        emit=emit,
        system_prompt_extras=system_prompt_extras,
    )
    try:
        try:
            outcome = planner.run(
                goal=goal,
                session_dir=executor.session_dir,
                trace_path=trace_path,
                executor=executor,
                prior_session_state=prior_session_state,
            )
        except MemoryHITLRequired as escalation:
            # PRD-07 Phase 3: the memory-informed policy refuses to
            # auto-dispatch a high-stakes action without evidence.
            # PRD-06 Phase D.2: we render a structured HITL prompt
            # via :func:`format_hitl_prompt` so the user sees the
            # escalation message, the proposed action, and what
            # memory had to say — not just the bare exception
            # string. The planner has already written
            # ``memory_trace.jsonl`` and ``memory_informed_policy``
            # trace events; the formatted prompt is the human-facing
            # surface.
            #
            # Round 7: ``new_case_onboarding`` carries a fully-rendered
            # chat block (the recommender's recommendations plus the
            # Y/n/customize prompt). Wrapping it in the structured HITL
            # template would double the question and dilute the call to
            # action, so we surface the chat block verbatim.
            ctx = getattr(escalation, "memory_context", None) or MemoryContext()
            decision_point = getattr(
                escalation, "decision_point", "unknown"
            )
            raw_message = (
                getattr(escalation, "message", "") or str(escalation)
            )
            if decision_point == "new_case_onboarding":
                final_text = raw_message
            else:
                final_text = format_hitl_prompt(
                    raw_message,
                    ctx,
                    decision_point=decision_point,
                    proposed_action=getattr(
                        escalation, "proposed_action", None
                    ),
                )
            write_event(
                trace_path,
                {
                    "event": "memory_hitl_required",
                    "escalation": getattr(escalation, "message", "")
                    or str(escalation),
                    "decision_point": getattr(
                        escalation, "decision_point", "unknown"
                    ),
                },
            )
            outcome = PlannerRun(
                ok=False,
                plan=[],
                results=executor.results,
                final_text=final_text,
            )
    finally:
        # Always tear down the per-executor spinner so the next print
        # starts on a clean line.
        executor.close()
    state_path, context_path = write_session_state(
        session_dir=executor.session_dir,
        goal=goal,
        planner="openai",
        model=model,
        allowed_tools=registry.sorted_names(),
        outcome=outcome,
    )
    write_event(trace_path, {"event": "session_state", "state": str(state_path), "context_summary": str(context_path)})

    # Runtime observability: record operational tool failures (MCP transport
    # drops, path-resolution errors, SWMM solver errors) to a dedicated
    # run_failures.jsonl so the real failure distribution is queryable and
    # future fixes can be data-driven. Deliberately separate from
    # negative_lessons (modeling knowledge) so operational noise never
    # pollutes that recall path. Best-effort: a recording failure must never
    # change the turn's outcome, so the whole block is swallowed.
    try:
        from agentic_swmm.memory.run_failures import (
            record_run_failures,
            resolve_store,
        )

        recorded = record_run_failures(
            resolve_store(),
            run_id=executor.session_dir.name,
            results=outcome.results,
        )
        if recorded:
            write_event(
                trace_path,
                {"event": "run_failures_recorded", "count": len(recorded)},
            )
    except Exception as exc:  # noqa: BLE001 - observability must not break the turn
        write_event(trace_path, {"event": "run_failures_error", "error": str(exc)})

    return outcome
