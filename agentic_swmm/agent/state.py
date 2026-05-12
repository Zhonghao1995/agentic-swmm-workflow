from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent.intent_map import intent_contracts, select_relevant_intents


def write_session_state(
    *,
    session_dir: Path,
    goal: str,
    planner: str,
    model: str | None,
    allowed_tools: list[str],
    outcome: Any,
) -> tuple[Path, Path]:
    summary = _summarize_context(outcome.results)
    failures = [result for result in outcome.results if not result.get("ok")]
    missing_prompts = _missing_input_prompts(outcome.results)
    workflow_state = _workflow_state(goal, outcome)
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "goal": goal,
        "planner": planner,
        "model": model,
        "ok": bool(outcome.ok),
        "allowed_tools": allowed_tools,
        "plan": [{"tool": call.name, "args": call.args} for call in outcome.plan],
        "tool_result_count": len(outcome.results),
        "failures": [_compact_result(result) for result in failures],
        "missing_input_prompts": missing_prompts,
        "retry_policy": {
            "tool_failure": "Return the failure summary to the planner once; retry only with corrected arguments or a different allowed tool.",
            "missing_inputs": "Stop and ask for concrete paths/materials instead of inventing SWMM inputs.",
        },
        "intent_contracts": intent_contracts(goal),
        "workflow_state": workflow_state,
        "context_summary": summary,
    }
    state_path = session_dir / "session_state.json"
    context_path = session_dir / "context_summary.md"
    state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    context_path.write_text(_context_markdown(payload), encoding="utf-8")
    return state_path, context_path


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "tool": result.get("tool"),
        "ok": bool(result.get("ok")),
        "summary": result.get("summary"),
    }
    if result.get("return_code") is not None:
        compact["return_code"] = result.get("return_code")
    if result.get("recovery"):
        compact["recovery"] = result.get("recovery")
    return {key: value for key, value in compact.items() if value is not None}


def _missing_input_prompts(results: list[dict[str, Any]]) -> list[str]:
    prompts: list[str] = []
    for result in results:
        payload = result.get("results")
        if not isinstance(payload, dict):
            continue
        prompt = payload.get("user_prompt")
        missing = payload.get("missing_inputs")
        if prompt and missing:
            prompts.append(str(prompt))
    return prompts


def _summarize_context(results: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for result in results[-8:]:
        tool = result.get("tool", "tool")
        status = "OK" if result.get("ok") else "FAILED"
        summary = result.get("summary") or result.get("stderr_tail") or result.get("stdout_tail") or "completed"
        lines.append(f"{tool}: {status} - {str(summary)[:300]}")
    return lines


def _workflow_state(goal: str, outcome: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "selected_intents": [str(intent.get("id")) for intent in select_relevant_intents(goal) if intent.get("id")],
        "selected_case": None,
        "active_run_dir": None,
        "available_plot_options": {},
        "selected_node": None,
        "selected_variable": None,
        "selected_rainfall": None,
        "pending_user_choice": None,
        "completed_artifacts": [],
    }
    for call in getattr(outcome, "plan", []):
        args = getattr(call, "args", {})
        if not isinstance(args, dict):
            continue
        if call.name == "run_swmm_inp":
            state["selected_case"] = args.get("inp_path") or state["selected_case"]
            state["active_run_dir"] = args.get("run_dir") or state["active_run_dir"]
        elif call.name == "audit_run":
            state["active_run_dir"] = args.get("run_dir") or state["active_run_dir"]
        elif call.name == "plot_run":
            state["active_run_dir"] = args.get("run_dir") or state["active_run_dir"]
            state["selected_node"] = args.get("node") or state["selected_node"]
            state["selected_variable"] = args.get("node_attr") or state["selected_variable"]
            state["selected_rainfall"] = args.get("rain_ts") or state["selected_rainfall"]
            if args.get("out_png"):
                _append_artifact(state, "plot", str(args["out_png"]))

    for result in getattr(outcome, "results", []):
        tool = str(result.get("tool") or "")
        payload = result.get("results")
        if tool == "select_workflow_mode" and isinstance(payload, dict):
            state["active_run_dir"] = payload.get("provided_values", {}).get("run_dir") or state["active_run_dir"]
            state["selected_case"] = payload.get("provided_values", {}).get("inp_path") or state["selected_case"]
            if payload.get("missing_inputs"):
                state["pending_user_choice"] = payload.get("user_prompt")
        elif tool == "inspect_plot_options" and isinstance(payload, dict):
            state["available_plot_options"] = {
                "rainfall": [item.get("name") for item in payload.get("rainfall_options", []) if isinstance(item, dict)],
                "nodes": list(payload.get("node_options", []))[:50],
                "node_attributes": [item.get("name") for item in payload.get("node_attribute_options", []) if isinstance(item, dict)],
                "defaults": payload.get("defaults", {}),
            }
            if payload.get("selections_needed"):
                state["pending_user_choice"] = payload.get("user_prompt") or "Choose plot options before plotting."
        elif tool == "run_swmm_inp" and result.get("ok"):
            _append_artifact(state, "run", str(state.get("active_run_dir") or ""))
        elif tool == "audit_run" and result.get("ok"):
            _append_artifact(state, "audit", _summary_path(str(result.get("summary") or ""), "audit_note"))
        elif tool == "plot_run" and result.get("ok"):
            _append_artifact(state, "plot", _summary_path(str(result.get("summary") or ""), "plot"))

    state["completed_artifacts"] = [item for item in state["completed_artifacts"] if item.get("path")]
    return state


def _append_artifact(state: dict[str, Any], kind: str, path: str) -> None:
    if not path:
        return
    artifact = {"kind": kind, "path": path}
    if artifact not in state["completed_artifacts"]:
        state["completed_artifacts"].append(artifact)


def _summary_path(summary: str, key: str) -> str:
    prefix = f"{key}="
    if prefix in summary:
        return summary.split(prefix, 1)[1].strip()
    if summary.startswith(f"{key}:"):
        return summary.split(":", 1)[1].strip()
    return ""


def _context_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Agentic SWMM Context Summary",
        "",
        f"- goal: {payload['goal']}",
        f"- planner: {payload['planner']}",
        f"- status: {'PASS' if payload['ok'] else 'FAIL'}",
        "",
        "## Recent Tool Context",
        "",
    ]
    context = payload.get("context_summary") or []
    lines.extend(f"- {line}" for line in context)
    workflow_state = payload.get("workflow_state") or {}
    if workflow_state:
        lines.extend(["", "## Workflow State", ""])
        for key in ("selected_intents", "selected_case", "active_run_dir", "selected_node", "selected_variable", "selected_rainfall", "pending_user_choice"):
            value = workflow_state.get(key)
            if value:
                lines.append(f"- {key}: {value}")
    if payload.get("missing_input_prompts"):
        lines.extend(["", "## Missing Inputs", ""])
        lines.extend(f"- {prompt}" for prompt in payload["missing_input_prompts"])
    if payload.get("failures"):
        lines.extend(["", "## Failures", ""])
        for failure in payload["failures"]:
            lines.append(f"- {failure.get('tool')}: {failure.get('summary')}")
    lines.append("")
    return "\n".join(lines)
