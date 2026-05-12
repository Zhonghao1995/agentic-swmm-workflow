from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    if payload.get("missing_input_prompts"):
        lines.extend(["", "## Missing Inputs", ""])
        lines.extend(f"- {prompt}" for prompt in payload["missing_input_prompts"])
    if payload.get("failures"):
        lines.extend(["", "## Failures", ""])
        for failure in payload["failures"]:
            lines.append(f"- {failure.get('tool')}: {failure.get('summary')}")
    lines.append("")
    return "\n".join(lines)
