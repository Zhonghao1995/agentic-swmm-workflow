from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.prompts import openai_planner_prompt
from agentic_swmm.agent.reporting import write_event
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.providers.openai_api import OpenAIProvider


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


class OpenAIPlanner:
    def __init__(self, provider: OpenAIProvider, registry: AgentToolRegistry, *, max_steps: int, verbose: bool = False, emit: Callable[[str], None] | None = None) -> None:
        self.provider = provider
        self.registry = registry
        self.max_steps = max_steps
        self.verbose = verbose
        self.emit = emit or (lambda text: None)

    def run(self, *, goal: str, session_dir: Path, trace_path: Path, executor: AgentExecutor) -> PlannerRun:
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
        plan: list[ToolCall] = []
        final_text = ""
        ok = True

        for step in range(1, self.max_steps + 1):
            response = self.provider.respond_with_tools(
                system_prompt=openai_planner_prompt(),
                input_items=input_items,
                tools=self.registry.schemas(),
                previous_response_id=previous_response_id,
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
                outputs.append({"type": "function_call_output", "call_id": provider_call.call_id, "output": json.dumps(self.registry.output_for_model(result), sort_keys=True)})
                if not result.get("ok"):
                    ok = False
                    break
            input_items = outputs
            if executor.dry_run or not ok:
                break
        else:
            ok = False
            final_text = f"planner stopped after max_steps={self.max_steps}"

        return PlannerRun(ok=ok, plan=plan, results=executor.results, final_text=final_text)
