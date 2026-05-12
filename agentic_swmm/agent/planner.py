from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.prompts import openai_planner_prompt
from agentic_swmm.agent.reporting import write_event
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.providers.openai_api import OpenAIProvider
from agentic_swmm.utils.paths import repo_root


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
        plan: list[ToolCall] = []
        auto_router_enabled = os.environ.get("AISWMM_DISABLE_AUTO_WORKFLOW_ROUTER") != "1"
        if os.environ.get("AISWMM_OPENAI_MOCK_TOOL_CALLS") and os.environ.get("AISWMM_FORCE_AUTO_WORKFLOW_ROUTER") != "1":
            auto_router_enabled = False
        if _looks_like_swmm_request(goal) and auto_router_enabled:
            route_call = ToolCall("select_workflow_mode", _workflow_route_args(goal))
            plan.append(route_call)
            self.emit("[1] select_workflow_mode")
            route_result = executor.execute(route_call, index=1)
            self.emit(f"OK: {route_result.get('summary') or 'completed'}")
            route = route_result.get("results") if isinstance(route_result.get("results"), dict) else {}
            if route.get("missing_inputs"):
                final_text = str(route.get("user_prompt") or "Please provide the missing SWMM workflow inputs.")
                return PlannerRun(ok=True, plan=plan, results=executor.results, final_text=final_text)

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


def _looks_like_swmm_request(goal: str) -> bool:
    lowered = goal.lower()
    if any(word in lowered for word in ("test", "pytest", "skill", "capabilities", "runtime", "diff", "search")):
        return False
    return any(
        word in lowered
        for word in (
            "swmm",
            ".inp",
            "audit",
            "plot",
            "calibration",
            "calibrate",
            "uncertainty",
            "fuzzy",
            "rainfall",
            "outfall",
            "node",
            "tecnopolo",
            "example",
            "examples/",
            "运行",
            "审计",
            "校准",
            "率定",
            "不确定",
        )
    )


def _workflow_route_args(goal: str) -> dict[str, Any]:
    args: dict[str, Any] = {"goal": goal}
    inp = _extract_inp_path(goal) or _extract_example_inp_path(goal)
    if inp:
        args["inp_path"] = inp
    node = _extract_after_label(goal, ("node", "outfall", "节点", "出口"))
    if node:
        args["node"] = node
    return args


def _extract_inp_path(text: str) -> str | None:
    quoted = re.search(r"[\"']([^\"']+\.inp)[\"']", text, flags=re.I)
    if quoted:
        return quoted.group(1)
    match = re.search(r"([A-Za-z]:\\[^\n\r]+?\.inp|(?:\.{0,2}/)?[^\s\"']+\.inp)", text, flags=re.I)
    return match.group(1).rstrip(".,;)]}") if match else None


def _extract_example_inp_path(text: str) -> str | None:
    match = re.search(r"(examples/[^\s，。；;,)]+)", text, flags=re.I)
    if not match:
        return None
    raw = match.group(1).rstrip("/.,;)]}。")
    candidate = (repo_root() / raw).resolve()
    if candidate.is_file() and candidate.suffix.lower() == ".inp":
        return raw
    if candidate.is_dir():
        matches = sorted(path for path in candidate.glob("*.inp") if path.is_file())
        if len(matches) == 1:
            return str(matches[0].resolve().relative_to(repo_root().resolve()))
    return raw


def _extract_after_label(text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:=]\s*([A-Za-z0-9_.-]+)", text, flags=re.I)
        if match:
            return match.group(1)
    return None
