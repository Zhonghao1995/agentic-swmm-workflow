from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.executor import AgentExecutor
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
        outcome = planner.run(
            goal=goal,
            session_dir=executor.session_dir,
            trace_path=trace_path,
            executor=executor,
            prior_session_state=prior_session_state,
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
    return outcome
