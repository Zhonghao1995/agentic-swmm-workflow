from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable

from agentic_swmm.agent.continuation_classifier import ExecutionPath, classify
from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.intent_map import looks_like_plot_request, looks_like_swmm_request, select_relevant_mcp_servers, select_relevant_skills
from agentic_swmm.agent.planner_introspection import should_introspect
from agentic_swmm.agent.prompts import openai_planner_prompt
from agentic_swmm.agent.reporting import write_event
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.agent.ui import Spinner, SpinnerState
from agentic_swmm.audit.llm_calls import extract_usage_tokens, record_llm_call
from agentic_swmm.providers.openai_api import OpenAIProvider
from agentic_swmm.utils.paths import repo_root


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
            return self._run_existing_run_plot_workflow(
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
            route_call = ToolCall("select_workflow_mode", _workflow_route_args(goal))
            plan.append(route_call)
            self.emit(f"[{len(plan)}] select_workflow_mode")
            route_result = executor.execute(route_call, index=len(plan))
            self.emit(f"OK: {route_result.get('summary') or 'completed'}")
            route = route_result.get("results") if isinstance(route_result.get("results"), dict) else {}
            if route.get("missing_inputs"):
                final_text = str(route.get("user_prompt") or "Please provide the missing SWMM workflow inputs.")
                return PlannerRun(ok=True, plan=plan, results=executor.results, final_text=final_text)
            if route.get("mode") == "prepared_inp_cli":
                return self._run_prepared_inp_workflow(
                    goal=goal,
                    session_dir=session_dir,
                    plan=plan,
                    route=route,
                    executor=executor,
                )
            if route.get("mode") == "existing_run_plot":
                return self._run_existing_run_plot_workflow(
                    goal=goal,
                    session_dir=session_dir,
                    plan=plan,
                    route=route,
                    executor=executor,
                )
            if route.get("mode") == "audit_only_or_comparison":
                return self._run_audit_followup_workflow(
                    goal=goal,
                    plan=plan,
                    route=route,
                    executor=executor,
                )

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

    def _run_prepared_inp_workflow(
        self,
        *,
        goal: str,
        session_dir: Path,
        plan: list[ToolCall],
        route: dict[str, Any],
        executor: AgentExecutor,
    ) -> PlannerRun:
        inp_path = str(route.get("provided_values", {}).get("inp_path") or _workflow_route_args(goal).get("inp_path") or "")
        if not inp_path:
            return PlannerRun(ok=True, plan=plan, results=executor.results, final_text="Please provide a SWMM INP path before running.")

        def execute(call: ToolCall) -> dict[str, Any]:
            plan.append(call)
            self.emit(f"[{len(plan)}] {call.name}")
            result = executor.execute(call, index=len(plan))
            status = "OK" if result.get("ok") else "FAILED"
            self.emit(f"{status}: {result.get('summary') or 'completed'}")
            return result

        node = _extract_after_label(goal, ("node", "outfall", "节点", "出口"))
        run_args = {"inp_path": inp_path, "run_id": session_dir.name, "run_dir": str(session_dir)}
        if node:
            run_args["node"] = node
        run_result = execute(ToolCall("run_swmm_inp", run_args))
        if not run_result.get("ok"):
            return PlannerRun(ok=False, plan=plan, results=executor.results, final_text="SWMM run failed; inspect the saved stderr/stdout artifacts.")

        audit_result = execute(
            ToolCall(
                "audit_run",
                {
                    "run_dir": str(session_dir),
                    "workflow_mode": "prepared_inp_cli",
                    "objective": goal,
                },
            )
        )
        if not audit_result.get("ok"):
            return PlannerRun(ok=False, plan=plan, results=executor.results, final_text="SWMM ran, but audit generation failed; inspect the saved audit tool artifacts.")

        options_result = execute(ToolCall("inspect_plot_options", {"run_dir": str(session_dir)}))
        if not options_result.get("ok"):
            return PlannerRun(ok=False, plan=plan, results=executor.results, final_text="SWMM ran and audit passed, but plot option inspection failed.")

        options = options_result.get("results") if isinstance(options_result.get("results"), dict) else {}
        plot_choice = _extract_plot_choice(goal, options)
        if plot_choice is None:
            return PlannerRun(
                ok=True,
                plan=plan,
                results=executor.results,
                final_text=_plot_choice_prompt(session_dir, options),
            )

        plot_path = _plot_output_path(session_dir, plot_choice)
        plot_args = {"run_dir": str(session_dir), **plot_choice, "out_png": str(plot_path)}
        plot_result = execute(ToolCall("plot_run", plot_args))
        if not plot_result.get("ok"):
            return PlannerRun(ok=False, plan=plan, results=executor.results, final_text="SWMM ran and audit passed, but plot generation failed.")
        return PlannerRun(
            ok=True,
            plan=plan,
            results=executor.results,
            final_text=_prepared_inp_done_text(session_dir, plot_path=plot_path),
        )

    def _run_existing_run_plot_workflow(
        self,
        *,
        goal: str,
        session_dir: Path,
        plan: list[ToolCall],
        route: dict[str, Any],
        executor: AgentExecutor,
    ) -> PlannerRun:
        run_dir = str(route.get("provided_values", {}).get("run_dir") or session_dir)

        def execute(call: ToolCall) -> dict[str, Any]:
            plan.append(call)
            self.emit(f"[{len(plan)}] {call.name}")
            result = executor.execute(call, index=len(plan))
            status = "OK" if result.get("ok") else "FAILED"
            self.emit(f"{status}: {result.get('summary') or 'completed'}")
            return result

        options_result = execute(ToolCall("inspect_plot_options", {"run_dir": run_dir}))
        if not options_result.get("ok"):
            return PlannerRun(ok=False, plan=plan, results=executor.results, final_text="Plot option inspection failed for the previous run directory.")

        options = options_result.get("results") if isinstance(options_result.get("results"), dict) else {}
        plot_choice = _extract_plot_choice(goal, options)
        if plot_choice is None:
            return PlannerRun(ok=True, plan=plan, results=executor.results, final_text=_plot_choice_prompt(Path(run_dir), options))

        plot_path = _plot_output_path(Path(run_dir), plot_choice)
        plot_result = execute(ToolCall("plot_run", {"run_dir": run_dir, **plot_choice, "out_png": str(plot_path)}))
        if not plot_result.get("ok"):
            return PlannerRun(ok=False, plan=plan, results=executor.results, final_text="Plot generation failed for the previous run directory.")
        return PlannerRun(
            ok=True,
            plan=plan,
            results=executor.results,
            final_text=_existing_run_plot_done_text(Path(run_dir), plot_choice, plot_path=plot_path),
        )

    def _run_audit_followup_workflow(
        self,
        *,
        goal: str,
        plan: list[ToolCall],
        route: dict[str, Any],
        executor: AgentExecutor,
    ) -> PlannerRun:
        run_dir = str(route.get("provided_values", {}).get("run_dir") or "")
        if not run_dir:
            return PlannerRun(ok=True, plan=plan, results=executor.results, final_text="Please provide a run directory to audit.")
        call = ToolCall("audit_run", {"run_dir": run_dir, "workflow_mode": "audit_only_or_comparison", "objective": goal})
        plan.append(call)
        self.emit(f"[{len(plan)}] {call.name}")
        result = executor.execute(call, index=len(plan))
        status = "OK" if result.get("ok") else "FAILED"
        self.emit(f"{status}: {result.get('summary') or 'completed'}")
        if not result.get("ok"):
            return PlannerRun(ok=False, plan=plan, results=executor.results, final_text="Audit failed; inspect the saved audit tool artifacts.")
        return PlannerRun(ok=True, plan=plan, results=executor.results, final_text=f"Audit completed for {run_dir}.")


def _looks_like_swmm_request(goal: str) -> bool:
    return looks_like_swmm_request(goal)


def _looks_like_plot_request(goal: str) -> bool:
    return looks_like_plot_request(goal)


def _select_relevant_skills(goal: str) -> list[str]:
    return select_relevant_skills(goal)


def _select_relevant_mcp_servers(skill_names: list[str]) -> list[str]:
    return select_relevant_mcp_servers(skill_names)


def _workflow_route_args(goal: str) -> dict[str, Any]:
    args: dict[str, Any] = {"goal": goal}
    run_dir = _extract_run_dir(goal)
    if run_dir:
        args["run_dir"] = run_dir
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


def _extract_run_dir(text: str) -> str | None:
    labelled = re.search(r"(?:run_dir|run folder|run directory|previous run directory|上一轮运行目录|运行目录)\s*[:=]\s*([^\n\r]+)", text, flags=re.I)
    if labelled:
        return labelled.group(1).strip().rstrip(".,;)]}。")
    match = re.search(r"(runs/[^\s，。；;,)]+)", text, flags=re.I)
    return match.group(1).rstrip(".,;)]}。") if match else None


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
            return matches[0].resolve().relative_to(repo_root().resolve()).as_posix()
    return raw


def _extract_after_label(text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:=]\s*([A-Za-z0-9_.-]+)", text, flags=re.I)
        if match:
            return match.group(1)
    return None


def _extract_plot_choice(goal: str, options: dict[str, Any]) -> dict[str, str] | None:
    lowered = goal.lower()
    explicit_plot = any(word in lowered for word in ("plot", "figure", "图", "画"))
    attrs = [str(item.get("name")) for item in options.get("node_attribute_options", []) if isinstance(item, dict)]
    nodes = [str(item) for item in options.get("node_options", [])]
    rains = [str(item.get("name")) for item in options.get("rainfall_options", []) if isinstance(item, dict)]

    node_attr = next((attr for attr in attrs if attr.lower() in lowered and not _is_negated(lowered, attr.lower())), None)
    if node_attr is None:
        aliases = {
            "depth": "Depth_above_invert",
            "水深": "Depth_above_invert",
            "volume": "Volume_stored_ponded",
            "体积": "Volume_stored_ponded",
            "flood": "Flow_lost_flooding",
            "flooding": "Flow_lost_flooding",
            "淹没": "Flow_lost_flooding",
            "溢流": "Flow_lost_flooding",
            "head": "Hydraulic_head",
            "水头": "Hydraulic_head",
            "flow": "Total_inflow",
            "peak": "Total_inflow",
            "流量": "Total_inflow",
            "峰值": "Total_inflow",
        }
        node_attr = next((value for key, value in aliases.items() if key in lowered and not _is_negated(lowered, key) and value in attrs), None)
    node = next((candidate for candidate in nodes if candidate.lower() in lowered), None)
    rain_ts = next((candidate for candidate in rains if candidate.lower() in lowered), None)

    if _asks_for_plot_options(lowered) and node_attr is None:
        return None
    if not explicit_plot and node_attr is None:
        return None
    defaults = options.get("defaults") if isinstance(options.get("defaults"), dict) else {}
    choice = {
        "node": node or str(defaults.get("node") or (nodes[0] if nodes else "O1")),
        "node_attr": node_attr or str(defaults.get("node_attr") or "Total_inflow"),
    }
    if rain_ts or defaults.get("rain_ts"):
        choice["rain_ts"] = rain_ts or str(defaults["rain_ts"])
    rain_kind = _default_rain_kind(options, choice.get("rain_ts"))
    if rain_kind:
        choice["rain_kind"] = rain_kind
    return choice


def _asks_for_plot_options(lowered: str) -> bool:
    return any(
        phrase in lowered
        for phrase in (
            "作图选项",
            "绘图选项",
            "别的图",
            "其他图",
            "换个图",
            "自己选",
            "我自己选",
            "有哪些图",
            "能画别的",
            "不想要",
            "不要 peak",
            "不要peak",
            "not peak",
            "not total_inflow",
        )
    )


def _is_negated(lowered: str, term: str) -> bool:
    start = lowered.find(term)
    if start < 0:
        return False
    prefix = lowered[max(0, start - 12) : start]
    return any(marker in prefix for marker in ("不想要", "不要", "别画", "不是", "not ", "no "))


def _default_rain_kind(options: dict[str, Any], rain_ts: str | None) -> str | None:
    for item in options.get("rainfall_options", []):
        if isinstance(item, dict) and item.get("name") == rain_ts and item.get("rain_kind"):
            return str(item["rain_kind"])
    return None


def _plot_choice_prompt(session_dir: Path, options: dict[str, Any]) -> str:
    defaults = options.get("defaults") if isinstance(options.get("defaults"), dict) else {}
    nodes = [str(item) for item in options.get("node_options", [])]
    attrs = [str(item.get("name")) for item in options.get("node_attribute_options", []) if isinstance(item, dict)]
    rains = [str(item.get("name")) for item in options.get("rainfall_options", []) if isinstance(item, dict)]
    node_preview = ", ".join(nodes[:8]) + (" ..." if len(nodes) > 8 else "")
    attr_preview = ", ".join(attrs[:8])
    rain_preview = ", ".join(rains) if rains else "auto"
    return (
        "SWMM run and audit completed successfully.\n\n"
        f"Run folder: {session_dir}\n"
        f"Audit note: {session_dir / 'experiment_note.md'}\n\n"
        "Before plotting, choose what you want to see:\n"
        f"- rainfall series: {rain_preview}\n"
        f"- node/outfall options: {node_preview}\n"
        f"- plot variable options: {attr_preview}\n\n"
        "Common choices are `Total_inflow` for flow/peak hydrograph, `Depth_above_invert` for node water depth, "
        "`Volume_stored_ponded` for stored volume, and `Flow_lost_flooding` for flooding loss.\n\n"
        f"Default suggestion: node `{defaults.get('node')}`, variable `{defaults.get('node_attr')}`, rainfall `{defaults.get('rain_ts')}`. "
        "Reply with the node and variable you want to plot."
    )


def _plot_output_path(run_dir: Path, choice: dict[str, str]) -> Path:
    node = re.sub(r"[^A-Za-z0-9_.-]+", "_", choice.get("node", "node")).strip("_") or "node"
    attr = re.sub(r"[^A-Za-z0-9_.-]+", "_", choice.get("node_attr", "series")).strip("_") or "series"
    return run_dir / "07_plots" / f"fig_{node}_{attr}.png"


def _prepared_inp_done_text(session_dir: Path, *, plot_path: Path | None = None) -> str:
    plot_line = f"Plot: {plot_path}" if plot_path else "Plot: not generated"
    return (
        "SWMM run, audit, and plotting completed successfully.\n\n"
        f"Run folder: {session_dir}\n"
        f"Audit note: {session_dir / 'experiment_note.md'}\n"
        f"{plot_line}\n\n"
        "Evidence boundary: this is runnable/auditable SWMM evidence, not calibration or validation unless observed-data checks are added."
    )


def _existing_run_plot_done_text(run_dir: Path, choice: dict[str, str], *, plot_path: Path) -> str:
    details = ", ".join(f"{key}={value}" for key, value in choice.items())
    return (
        "Plot completed from the previous SWMM run.\n\n"
        f"Run folder: {run_dir}\n"
        f"Plot: {plot_path}\n"
        f"Selection: {details}\n\n"
        "Evidence boundary: the plot was generated from the existing run artifacts."
    )


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
