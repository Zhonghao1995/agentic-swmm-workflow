from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.config import load_config
from agentic_swmm.providers.base import ProviderToolCall
from agentic_swmm.providers.openai_api import OpenAIProvider
from agentic_swmm.utils.paths import repo_root, script_path
from agentic_swmm.utils.subprocess_runner import runtime_env

ALLOWED_TOOLS = {"doctor", "demo_acceptance", "audit_run", "summarize_memory", "read_file"}


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("agent", help="Run a constrained local Agentic SWMM executor.")
    parser.add_argument("goal", nargs="*", help="Goal for the local executor.")
    parser.add_argument("--planner", choices=["rule", "openai"], default="rule", help="Planner backend. Defaults to the deterministic rule planner.")
    parser.add_argument("--provider", choices=["openai"], help="Provider to use with --planner openai. Defaults to config provider.default.")
    parser.add_argument("--model", help="Model override for --planner openai.")
    parser.add_argument("--session-id", help="Stable session id. Defaults to a timestamped id.")
    parser.add_argument("--session-dir", type=Path, help="Directory for trace, tool outputs, and final report.")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; do not execute tools.")
    parser.add_argument("--max-steps", type=int, default=8, help="Maximum tool calls to execute.")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    goal = " ".join(args.goal).strip() or "run doctor"
    session_id = args.session_id or f"agent-{int(time.time())}"
    session_dir = args.session_dir.expanduser().resolve() if args.session_dir else repo_root() / "runs" / "agent" / _safe_name(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    trace_path = session_dir / "agent_trace.jsonl"
    if args.planner == "openai":
        return _run_openai_planner(args, goal, session_dir, trace_path)

    plan = _plan(goal)
    if len(plan) > args.max_steps:
        plan = plan[: args.max_steps]
    _write_event(trace_path, {"event": "session_start", "goal": goal, "session_dir": str(session_dir), "plan": [_call_payload(c) for c in plan]})

    print("Agentic SWMM executor")
    print(f"- goal: {goal}")
    print(f"- session: {session_dir}")
    print("- plan:")
    for index, call in enumerate(plan, start=1):
        print(f"  {index}. {call.name} {json.dumps(call.args, sort_keys=True)}")

    results: list[dict[str, Any]] = []
    if args.dry_run:
        _write_report(session_dir, goal, plan, results, dry_run=True)
        print(f"Dry run only. Trace: {trace_path}")
        return 0

    ok = True
    for index, call in enumerate(plan, start=1):
        print(f"\n[{index}/{len(plan)}] {call.name}")
        _write_event(trace_path, {"event": "tool_start", "index": index, **_call_payload(call)})
        result = _execute_tool(call, session_dir)
        results.append(result)
        _write_event(trace_path, {"event": "tool_result", "index": index, **result})
        if result["ok"]:
            detail = result.get("summary") or result.get("stdout_tail") or "ok"
            print(f"OK: {detail}")
        else:
            ok = False
            print(f"FAILED: {result.get('summary') or result.get('stderr_tail') or 'tool failed'}")
            break

    report = _write_report(session_dir, goal, plan, results, dry_run=False)
    _write_event(trace_path, {"event": "session_end", "ok": ok, "report": str(report)})
    print(f"\nFinal report: {report}")
    return 0 if ok else 1


def _run_openai_planner(args: argparse.Namespace, goal: str, session_dir: Path, trace_path: Path) -> int:
    config = load_config()
    provider_name = args.provider or config.get("provider.default", "openai")
    model = args.model or config.get(f"{provider_name}.model")
    if provider_name != "openai":
        raise ValueError(f"unsupported planner provider: {provider_name}")
    if not model:
        raise ValueError("OpenAI model is not configured. Run `aiswmm model --provider openai --model gpt-5.5`.")

    provider = OpenAIProvider(model=model)
    system_prompt = _openai_planner_prompt()
    input_items: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"Goal: {goal}\n"
                f"Session directory: {session_dir}\n"
                "Use only the provided tools. Stop with a concise final answer after the evidence is sufficient."
            ),
        }
    ]
    previous_response_id: str | None = None
    plan: list[ToolCall] = []
    results: list[dict[str, Any]] = []
    final_text = ""
    ok = True

    _write_event(
        trace_path,
        {
            "event": "session_start",
            "goal": goal,
            "session_dir": str(session_dir),
            "planner": "openai",
            "model": model,
            "allowed_tools": sorted(ALLOWED_TOOLS),
        },
    )

    print("Agentic SWMM executor")
    print(f"- planner: openai")
    print(f"- model: {model}")
    print(f"- goal: {goal}")
    print(f"- session: {session_dir}")
    print(f"- allowed tools: {', '.join(sorted(ALLOWED_TOOLS))}")

    for step in range(1, args.max_steps + 1):
        response = provider.respond_with_tools(
            system_prompt=system_prompt,
            input_items=input_items,
            tools=_openai_tool_schemas(),
            previous_response_id=previous_response_id,
        )
        previous_response_id = response.response_id
        _write_event(
            trace_path,
            {
                "event": "planner_response",
                "step": step,
                "response_id": response.response_id,
                "text": response.text,
                "tool_calls": [_provider_call_payload(call) for call in response.tool_calls],
            },
        )
        if not response.tool_calls:
            final_text = response.text.strip()
            break

        outputs: list[dict[str, Any]] = []
        for provider_call in response.tool_calls:
            call = _validated_openai_call(provider_call)
            plan.append(call)
            print(f"\n[{len(plan)}] {call.name} {json.dumps(call.args, sort_keys=True)}")
            _write_event(trace_path, {"event": "tool_start", "index": len(plan), **_call_payload(call)})
            if args.dry_run:
                result = {"tool": call.name, "args": call.args, "ok": True, "summary": "dry run; tool not executed"}
            else:
                result = _execute_tool(call, session_dir)
            results.append(result)
            _write_event(trace_path, {"event": "tool_result", "index": len(plan), **result})
            print(("OK: " if result.get("ok") else "FAILED: ") + str(result.get("summary") or "completed"))
            outputs.append({"type": "function_call_output", "call_id": provider_call.call_id, "output": json.dumps(_tool_output_for_model(result), sort_keys=True)})
            if not result.get("ok"):
                ok = False
                break
        input_items = outputs
        if args.dry_run or not ok:
            break
    else:
        ok = False
        final_text = f"planner stopped after max_steps={args.max_steps}"

    report = _write_report(session_dir, goal, plan, results, dry_run=args.dry_run, planner="openai", final_text=final_text)
    _write_event(trace_path, {"event": "session_end", "ok": ok, "report": str(report), "final_text": final_text})
    print(f"\nFinal report: {report}")
    if final_text:
        print(final_text)
    return 0 if ok else 1


def _plan(goal: str) -> list[ToolCall]:
    text = goal.lower()
    calls: list[ToolCall] = []

    if any(word in text for word in ("doctor", "diagnose", "check setup", "runtime")):
        calls.append(ToolCall("doctor", {}))

    wants_acceptance = "acceptance" in text or "demo" in text
    wants_audit = "audit" in text
    wants_memory = "memory" in text or "summarize" in text
    wants_report = "report" in text or "summarize" in text

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


def _openai_planner_prompt() -> str:
    return (
        "You are the Agentic SWMM tool-calling planner. "
        "Plan and execute with only the provided function tools: doctor, demo_acceptance, audit_run, summarize_memory, read_file. "
        "Never request shell commands, package installation, network access, file writes outside tool side effects, or tools not in the schema. "
        "Use doctor for runtime checks, demo_acceptance for a reproducible acceptance run, audit_run for evidence capture, "
        "summarize_memory for modeling-memory refreshes, and read_file for inspecting repository artifacts. "
        "After each tool result, decide the next evidence-producing tool or stop with a concise final answer that states the evidence boundary."
    )


def _openai_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "doctor",
            "description": "Run the built-in Agentic SWMM runtime doctor.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "demo_acceptance",
            "description": "Run the prepared acceptance demo through the Agentic SWMM CLI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "Run id under runs/acceptance."},
                    "keep_existing": {"type": "boolean", "description": "Keep an existing acceptance run directory."},
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "audit_run",
            "description": "Audit a run directory and write deterministic provenance/comparison/note artifacts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "run_dir": {"type": "string", "description": "Repository-relative run directory."},
                    "workflow_mode": {"type": "string", "description": "Workflow label, for example acceptance."},
                    "objective": {"type": "string", "description": "Run objective to record in the audit note."},
                },
                "required": ["run_dir"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "summarize_memory",
            "description": "Summarize audited runs into the modeling-memory directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "runs_dir": {"type": "string", "description": "Repository-relative runs directory to summarize."},
                    "out_dir": {"type": "string", "description": "Repository-relative modeling-memory output directory."},
                },
                "required": ["runs_dir"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "read_file",
            "description": "Read a repository file and return a bounded excerpt.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Repository-relative file path."}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    ]


def _validated_openai_call(call: ProviderToolCall) -> ToolCall:
    if call.name not in ALLOWED_TOOLS:
        raise ValueError(f"planner requested unsupported tool: {call.name}")
    return ToolCall(call.name, dict(call.arguments))


def _provider_call_payload(call: ProviderToolCall) -> dict[str, Any]:
    return {"call_id": call.call_id, "tool": call.name, "args": call.arguments}


def _tool_output_for_model(result: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "tool",
        "args",
        "ok",
        "return_code",
        "summary",
        "stdout_tail",
        "stderr_tail",
        "path",
        "chars",
        "excerpt",
    }
    return {key: value for key, value in result.items() if key in allowed_keys}


def _execute_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    if call.name == "doctor":
        return _run_cli_tool(call, session_dir, ["doctor"])
    if call.name == "demo_acceptance":
        command = ["demo", "acceptance", "--run-id", str(call.args.get("run_id", "agent-latest"))]
        if call.args.get("keep_existing"):
            command.append("--keep-existing")
        return _run_cli_tool(call, session_dir, command)
    if call.name == "audit_run":
        command = ["audit", "--run-dir", str(call.args["run_dir"])]
        if call.args.get("workflow_mode"):
            command.extend(["--workflow-mode", str(call.args["workflow_mode"])])
        if call.args.get("objective"):
            command.extend(["--objective", str(call.args["objective"])])
        return _run_cli_tool(call, session_dir, command)
    if call.name == "summarize_memory":
        command = ["memory", "--runs-dir", str(call.args["runs_dir"])]
        if call.args.get("out_dir"):
            command.extend(["--out-dir", str(call.args["out_dir"])])
        return _run_cli_tool(call, session_dir, command)
    if call.name == "read_file":
        return _read_file_tool(call)
    return {"tool": call.name, "args": call.args, "ok": False, "summary": f"unsupported tool: {call.name}"}


def _run_cli_tool(call: ToolCall, session_dir: Path, cli_args: list[str]) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    command = [sys.executable, "-m", "agentic_swmm.cli", *cli_args]
    proc = subprocess.run(command, cwd=repo_root(), capture_output=True, text=True, env=runtime_env())
    finished = datetime.now(timezone.utc)
    safe_name = _safe_name(call.name)
    stdout_path = session_dir / "tool_results" / f"{safe_name}.stdout.txt"
    stderr_path = session_dir / "tool_results" / f"{safe_name}.stderr.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    return {
        "tool": call.name,
        "args": call.args,
        "command": command,
        "ok": proc.returncode == 0,
        "return_code": proc.returncode,
        "started_at_utc": started.isoformat(timespec="seconds"),
        "finished_at_utc": finished.isoformat(timespec="seconds"),
        "stdout_file": str(stdout_path),
        "stderr_file": str(stderr_path),
        "stdout_tail": _tail(proc.stdout),
        "stderr_tail": _tail(proc.stderr),
        "summary": _summarize_cli_result(call.name, proc.stdout, proc.returncode),
    }


def _read_file_tool(call: ToolCall) -> dict[str, Any]:
    path = (repo_root() / str(call.args["path"])).resolve()
    try:
        path.relative_to(repo_root().resolve())
    except ValueError:
        return {"tool": call.name, "args": call.args, "ok": False, "summary": "refusing to read outside repository"}
    if not path.exists() or not path.is_file():
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"file not found: {path}"}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "path": str(path),
        "chars": len(text),
        "excerpt": text[:4000],
        "summary": f"read {path.relative_to(repo_root())}",
    }


def _summarize_cli_result(tool: str, stdout: str, return_code: int) -> str:
    if return_code != 0:
        return f"{tool} failed"
    parsed = _try_json(stdout)
    if isinstance(parsed, dict):
        if "run_dir" in parsed:
            return f"run_dir={parsed['run_dir']}"
        if "experiment_note" in parsed:
            return f"audit_note={parsed['experiment_note']}"
    stripped = stdout.strip().splitlines()
    return stripped[-1] if stripped else "completed"


def _write_report(
    session_dir: Path,
    goal: str,
    plan: list[ToolCall],
    results: list[dict[str, Any]],
    *,
    dry_run: bool,
    planner: str = "rule",
    final_text: str = "",
) -> Path:
    report_path = session_dir / "final_report.md"
    ok = all(result.get("ok") for result in results) if results else dry_run
    lines = [
        "# Agentic SWMM Executor Report",
        "",
        f"- goal: {goal}",
        f"- planner: {planner}",
        f"- status: {'DRY RUN' if dry_run else ('PASS' if ok else 'FAIL')}",
        f"- session_dir: {session_dir}",
        f"- allowed_tools: {', '.join(sorted(ALLOWED_TOOLS))}",
        "",
        "## Plan",
        "",
    ]
    for index, call in enumerate(plan, start=1):
        lines.append(f"{index}. `{call.name}` `{json.dumps(call.args, sort_keys=True)}`")
    if results:
        lines.extend(["", "## Tool Results", ""])
        for index, result in enumerate(results, start=1):
            lines.append(f"{index}. `{result['tool']}` - {'OK' if result.get('ok') else 'FAILED'}")
            if result.get("summary"):
                lines.append(f"   - summary: {result['summary']}")
            if result.get("stdout_file"):
                lines.append(f"   - stdout: {result['stdout_file']}")
            if result.get("stderr_file"):
                lines.append(f"   - stderr: {result['stderr_file']}")
            if result.get("path"):
                lines.append(f"   - artifact: {result['path']}")
    if final_text:
        lines.extend(["", "## Planner Final Answer", "", final_text])
    lines.extend(["", "## Evidence Boundary", "", "This executor only reports commands and artifacts it actually ran or read. A successful SWMM run is not a calibration or validation claim unless observed-data evidence and validation checks are present."])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _write_event(path: Path, payload: dict[str, Any]) -> None:
    payload = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _call_payload(call: ToolCall) -> dict[str, Any]:
    return {"tool": call.name, "args": call.args}


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _tail(text: str, max_chars: int = 2000) -> str:
    return text.strip()[-max_chars:]


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "agent"
