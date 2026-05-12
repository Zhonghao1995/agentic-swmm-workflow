from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.planner import OpenAIPlanner, PlannerRun, rule_plan
from agentic_swmm.agent.reporting import write_event
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
    for index, call in enumerate(plan, start=1):
        result = executor.execute(call, index=index)
        if not result.get("ok"):
            ok = False
            break
    return PlannerRun(ok=ok, plan=plan, results=executor.results, final_text="")


def run_openai_plan(*, goal: str, model: str, provider, registry: AgentToolRegistry, executor: AgentExecutor, max_steps: int, trace_path: Path, verbose: bool, emit) -> PlannerRun:
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
    planner = OpenAIPlanner(provider, registry, max_steps=max_steps, verbose=verbose, emit=emit)
    return planner.run(goal=goal, session_dir=executor.session_dir, trace_path=trace_path, executor=executor)
