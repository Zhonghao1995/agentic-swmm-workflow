"""Non-interactive single-shot executor for the ``aiswmm agent`` CLI.

This module owns the rule-planner code path (the ``--planner rule``
single-shot flow used by tests and by ``aiswmm agent <goal>`` without
``--interactive``). It also holds the historical tool-dispatch helpers
(``_execute_tool`` and friends) that predated the agent executor.
Those helpers are kept here unchanged so the public import surface and
existing tests (notably ``tests/test_agentic_swmm_cli.py`` which imports
``_find_repo_inp``) keep working after the runtime_loop split.

This split is a no-behaviour-change move: it was extracted from
``agentic_swmm/commands/agent.py`` together with
``agent/runtime_loop.py`` in the first commit of the Runtime UX PRD.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.planner import rule_plan
from agentic_swmm.agent.reporting import write_event as _write_event
from agentic_swmm.agent.reporting import write_report as _write_report
from agentic_swmm.agent.runtime import run_rule_plan
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.agent.ui import agent_say as _agent_say
from agentic_swmm.agent.ui import compact_plan as _compact_plan
from agentic_swmm.agent.ui import display_path as _display_path
from agentic_swmm.providers.base import ProviderToolCall
from agentic_swmm.runtime.registry import discover_skills
from agentic_swmm.utils.paths import repo_root, script_path
from agentic_swmm.utils.subprocess_runner import runtime_env

ALLOWED_TOOLS = AgentToolRegistry().names


def run_single_shot(args: argparse.Namespace) -> int:
    """Execute the non-interactive rule-planner flow."""
    goal = " ".join(args.goal).strip() or "run doctor"
    session_id = args.session_id or f"agent-{int(time.time())}"
    session_dir = (
        args.session_dir.expanduser().resolve()
        if args.session_dir
        else repo_root() / "runs" / "agent" / _safe_name(session_id)
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    trace_path = session_dir / "agent_trace.jsonl"
    registry = AgentToolRegistry()
    if args.planner == "openai":
        # Delegate to runtime_loop's openai planner for the single-shot path.
        from agentic_swmm.agent.runtime_loop import run_openai_planner

        return run_openai_planner(args, goal, session_dir, trace_path, registry)

    preview_plan = rule_plan(goal)
    if len(preview_plan) > args.max_steps:
        preview_plan = preview_plan[: args.max_steps]

    _agent_say("aiswmm executor")
    _agent_say(f"Goal: {goal}")
    _agent_say(f"Evidence folder: {_display_path(session_dir)}")
    if args.verbose:
        _agent_say("Plan:")
        for index, call in enumerate(preview_plan, start=1):
            _agent_say(f"  {index}. {call.name} {json.dumps(call.args, sort_keys=True)}")
    else:
        _agent_say(f"Plan: {_compact_plan(preview_plan)}")

    if args.dry_run:
        _write_report(session_dir, goal, preview_plan, [], dry_run=True, allowed_tools=registry.names)
        _agent_say(f"Dry run only. Trace: {_display_path(trace_path)}")
        return 0

    executor = AgentExecutor(registry, session_dir=session_dir, trace_path=trace_path, dry_run=False)
    outcome = run_rule_plan(
        goal=goal,
        registry=registry,
        executor=executor,
        max_steps=args.max_steps,
        trace_path=trace_path,
    )
    for index, (call, result) in enumerate(zip(outcome.plan, outcome.results), start=1):
        _agent_say(f"[{index}/{len(outcome.plan)}] {call.name}")
        if result["ok"]:
            detail = result.get("summary") or result.get("stdout_tail") or "ok"
            _agent_say(f"OK: {detail}")
        else:
            _agent_say(f"FAILED: {result.get('summary') or result.get('stderr_tail') or 'tool failed'}")
            break

    report = _write_report(
        session_dir,
        goal,
        outcome.plan,
        outcome.results,
        dry_run=False,
        allowed_tools=registry.names,
    )
    _write_event(trace_path, {"event": "session_end", "ok": outcome.ok, "report": str(report)})
    _agent_say(f"Final report: {_display_path(report)}")
    return 0 if outcome.ok else 1


# ---------------------------------------------------------------------------
# Historical tool-dispatch helpers (preserved verbatim from commands/agent.py).
# These predate the AgentExecutor and are not on the live path today, but
# tests import _find_repo_inp directly and the helpers are kept self-contained
# in this module for parity with the pre-split file.
# ---------------------------------------------------------------------------


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
            "name": "list_skills",
            "description": "List available repository skills.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "read_skill",
            "description": "Read a skill contract from skills/<skill_name>/SKILL.md.",
            "parameters": {
                "type": "object",
                "properties": {"skill_name": {"type": "string", "description": "Skill directory name, for example swmm-runner."}},
                "required": ["skill_name"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "run_swmm_inp",
            "description": "Run a repository .inp file through the constrained swmm-runner CLI wrapper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "inp_path": {"type": "string", "description": "Repository-relative .inp path or user-provided absolute .inp path to import into the run directory."},
                    "run_id": {"type": "string", "description": "Optional run id under runs/agent."},
                    "run_dir": {"type": "string", "description": "Optional repository-relative run directory."},
                    "node": {"type": "string", "description": "Node/outfall for peak-flow parsing."},
                },
                "required": ["inp_path"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "plot_run",
            "description": "Create a rainfall-runoff plot from a run directory using the swmm-plot skill wrapper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "run_dir": {"type": "string", "description": "Repository-relative run directory."},
                    "node": {"type": "string", "description": "Node/outfall to plot."},
                    "rain_ts": {"type": "string", "description": "Optional rainfall TIMESERIES name."},
                    "rain_kind": {
                        "type": "string",
                        "enum": ["intensity_mm_per_hr", "depth_mm_per_dt", "cumulative_depth_mm"],
                    },
                    "out_png": {"type": "string", "description": "Optional repository-relative PNG output path."},
                },
                "required": ["run_dir"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "network_qa",
            "description": "Validate a SWMM network JSON using the swmm-network QA script.",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_json": {"type": "string", "description": "Repository-relative network JSON path."},
                    "report_json": {"type": "string", "description": "Optional repository-relative QA report path."},
                },
                "required": ["network_json"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "network_to_inp",
            "description": "Export a SWMM network JSON to INP section text using the swmm-network script.",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_json": {"type": "string", "description": "Repository-relative network JSON path."},
                    "out_path": {"type": "string", "description": "Repository-relative output text path."},
                },
                "required": ["network_json", "out_path"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "format_rainfall",
            "description": "Format rainfall CSV into SWMM TIMESERIES text and metadata JSON using the swmm-climate skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "input_csv": {"type": "string", "description": "Repository-relative rainfall CSV path."},
                    "out_json": {"type": "string", "description": "Repository-relative output metadata JSON."},
                    "out_timeseries": {"type": "string", "description": "Repository-relative output TIMESERIES text."},
                    "series_name": {"type": "string"},
                    "timestamp_column": {"type": "string"},
                    "value_column": {"type": "string"},
                    "value_units": {"type": "string"},
                    "unit_policy": {"type": "string", "enum": ["strict", "convert_to_mm_per_hr"]},
                    "timestamp_policy": {"type": "string", "enum": ["strict", "sort"]},
                },
                "required": ["input_csv", "out_json", "out_timeseries"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "build_inp",
            "description": "Assemble a SWMM INP from explicit CSV/JSON/text inputs using the swmm-builder skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subcatchments_csv": {"type": "string"},
                    "params_json": {"type": "string"},
                    "network_json": {"type": "string"},
                    "rainfall_json": {"type": "string"},
                    "raingage_json": {"type": "string"},
                    "timeseries_text": {"type": "string"},
                    "config_json": {"type": "string"},
                    "default_gage_id": {"type": "string"},
                    "out_inp": {"type": "string"},
                    "out_manifest": {"type": "string"},
                },
                "required": ["subcatchments_csv", "params_json", "network_json", "out_inp", "out_manifest"],
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
    if call.name == "list_skills":
        return _list_skills_tool(call)
    if call.name == "read_skill":
        return _read_skill_tool(call)
    if call.name == "run_swmm_inp":
        return _run_swmm_inp_tool(call, session_dir)
    if call.name == "plot_run":
        return _plot_run_tool(call, session_dir)
    if call.name == "network_qa":
        return _network_qa_tool(call, session_dir)
    if call.name == "network_to_inp":
        return _network_to_inp_tool(call, session_dir)
    if call.name == "format_rainfall":
        return _format_rainfall_tool(call, session_dir)
    if call.name == "build_inp":
        return _build_inp_tool(call, session_dir)
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


def _run_script_tool(call: ToolCall, session_dir: Path, cli_args: list[str]) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    command = [sys.executable, *cli_args]
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
    path = _repo_path(str(call.args["path"]))
    if path is None:
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


def _list_skills_tool(call: ToolCall) -> dict[str, Any]:
    records = discover_skills()
    skills = [
        {
            "name": str(record.get("name")),
            "enabled": bool(record.get("enabled", True)),
            "path": str(record.get("path")),
        }
        for record in records
    ]
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "skills": skills,
        "summary": f"{len(skills)} skills available",
        "excerpt": json.dumps(skills, indent=2)[:4000],
    }


def _read_skill_tool(call: ToolCall) -> dict[str, Any]:
    skill_name = str(call.args["skill_name"])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", skill_name):
        return {"tool": call.name, "args": call.args, "ok": False, "summary": "invalid skill name"}
    path = _repo_path(f"skills/{skill_name}/SKILL.md")
    if path is None or not path.exists() or not path.is_file():
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"skill not found: {skill_name}"}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "path": str(path),
        "chars": len(text),
        "excerpt": text[:4000],
        "summary": f"read skill {skill_name}",
    }


def _run_swmm_inp_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    inp = _resolve_inp_for_run(call)
    if isinstance(inp, dict):
        return inp
    run_dir = _optional_repo_output_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir
    if run_dir is None:
        run_id = str(call.args.get("run_id") or f"{_safe_name(inp.stem)}-{int(time.time())}")
        run_dir = repo_root() / "runs" / "agent" / _safe_name(run_id)
    command = ["run", "--inp", str(inp), "--run-dir", str(run_dir), "--node", str(call.args.get("node") or "O1")]
    return _run_cli_tool(call, session_dir, command)


def _plot_run_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    run_dir = _required_repo_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir
    command = ["plot", "--run-dir", str(run_dir), "--node", str(call.args.get("node") or "O1")]
    if call.args.get("rain_ts"):
        command.extend(["--rain-ts", str(call.args["rain_ts"])])
    if call.args.get("rain_kind"):
        command.extend(["--rain-kind", str(call.args["rain_kind"])])
    if call.args.get("out_png"):
        out_png = _repo_output_path(str(call.args["out_png"]))
        if out_png is None or out_png.suffix.lower() != ".png":
            return {"tool": call.name, "args": call.args, "ok": False, "summary": "out_png must be a repository-relative .png path"}
        command.extend(["--out-png", str(out_png)])
    return _run_cli_tool(call, session_dir, command)


def _network_qa_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    network_json = _required_repo_file(call, "network_json", suffix=".json")
    if isinstance(network_json, dict):
        return network_json
    command = [str(script_path("skills", "swmm-network", "scripts", "network_qa.py")), str(network_json)]
    if call.args.get("report_json"):
        report = _repo_output_path(str(call.args["report_json"]))
        if report is None or report.suffix.lower() != ".json":
            return {"tool": call.name, "args": call.args, "ok": False, "summary": "report_json must be a repository-relative .json path"}
        command.extend(["--report-json", str(report)])
    return _run_script_tool(call, session_dir, command)


def _network_to_inp_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    network_json = _required_repo_file(call, "network_json", suffix=".json")
    if isinstance(network_json, dict):
        return network_json
    out_path = _repo_output_path(str(call.args["out_path"]))
    if out_path is None or out_path.suffix.lower() not in {".inp", ".txt"}:
        return {"tool": call.name, "args": call.args, "ok": False, "summary": "out_path must be a repository-relative .inp or .txt path"}
    command = [str(script_path("skills", "swmm-network", "scripts", "network_to_inp.py")), str(network_json), "--out", str(out_path)]
    return _run_script_tool(call, session_dir, command)


def _format_rainfall_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    input_csv = _required_repo_file(call, "input_csv", suffix=".csv")
    if isinstance(input_csv, dict):
        return input_csv
    out_json = _repo_output_path(str(call.args["out_json"]))
    out_timeseries = _repo_output_path(str(call.args["out_timeseries"]))
    if out_json is None or out_json.suffix.lower() != ".json":
        return {"tool": call.name, "args": call.args, "ok": False, "summary": "out_json must be a repository-relative .json path"}
    if out_timeseries is None or out_timeseries.suffix.lower() not in {".txt", ".dat"}:
        return {"tool": call.name, "args": call.args, "ok": False, "summary": "out_timeseries must be a repository-relative .txt or .dat path"}
    command = [
        str(script_path("skills", "swmm-climate", "scripts", "format_rainfall.py")),
        "--input",
        str(input_csv),
        "--out-json",
        str(out_json),
        "--out-timeseries",
        str(out_timeseries),
    ]
    for arg_name, flag in (
        ("series_name", "--series-name"),
        ("timestamp_column", "--timestamp-column"),
        ("value_column", "--value-column"),
        ("value_units", "--value-units"),
        ("unit_policy", "--unit-policy"),
        ("timestamp_policy", "--timestamp-policy"),
    ):
        if call.args.get(arg_name):
            command.extend([flag, str(call.args[arg_name])])
    return _run_script_tool(call, session_dir, command)


def _build_inp_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    required_files = {
        "subcatchments_csv": ".csv",
        "params_json": ".json",
        "network_json": ".json",
    }
    resolved: dict[str, Path] = {}
    for key, suffix in required_files.items():
        path = _required_repo_file(call, key, suffix=suffix)
        if isinstance(path, dict):
            return path
        resolved[key] = path

    out_inp = _repo_output_path(str(call.args["out_inp"]))
    out_manifest = _repo_output_path(str(call.args["out_manifest"]))
    if out_inp is None or out_inp.suffix.lower() != ".inp":
        return {"tool": call.name, "args": call.args, "ok": False, "summary": "out_inp must be a repository-relative .inp path"}
    if out_manifest is None or out_manifest.suffix.lower() != ".json":
        return {"tool": call.name, "args": call.args, "ok": False, "summary": "out_manifest must be a repository-relative .json path"}

    command = [
        str(script_path("skills", "swmm-builder", "scripts", "build_swmm_inp.py")),
        "--subcatchments-csv",
        str(resolved["subcatchments_csv"]),
        "--params-json",
        str(resolved["params_json"]),
        "--network-json",
        str(resolved["network_json"]),
        "--out-inp",
        str(out_inp),
        "--out-manifest",
        str(out_manifest),
    ]
    optional_files = {
        "rainfall_json": ("--rainfall-json", ".json"),
        "raingage_json": ("--raingage-json", ".json"),
        "timeseries_text": ("--timeseries-text", None),
        "config_json": ("--config-json", ".json"),
    }
    for key, (flag, suffix) in optional_files.items():
        if call.args.get(key):
            path = _required_repo_file(call, key, suffix=suffix)
            if isinstance(path, dict):
                return path
            command.extend([flag, str(path)])
    if call.args.get("default_gage_id"):
        command.extend(["--default-gage-id", str(call.args["default_gage_id"])])
    return _run_script_tool(call, session_dir, command)


def _summarize_cli_result(tool: str, stdout: str, return_code: int) -> str:
    if return_code != 0:
        return f"{tool} failed"
    parsed = _try_json(stdout)
    if isinstance(parsed, dict):
        if "run_dir" in parsed:
            return f"run_dir={parsed['run_dir']}"
        if "experiment_note" in parsed:
            return f"audit_note={parsed['experiment_note']}"
        if "ok" in parsed and "issue_count" in parsed:
            return f"ok={parsed['ok']} issue_count={parsed['issue_count']}"
        if "outputs" in parsed:
            return "outputs=" + json.dumps(parsed["outputs"], sort_keys=True)[:500]
    stripped = stdout.strip().splitlines()
    return stripped[-1] if stripped else "completed"


def _repo_path(value: str) -> Path | None:
    raw = Path(value).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (repo_root() / raw).resolve()
    try:
        candidate.relative_to(repo_root().resolve())
    except ValueError:
        return None
    return candidate


def _repo_output_path(value: str) -> Path | None:
    path = _repo_path(value)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _required_repo_file(call: ToolCall, key: str, *, suffix: str | None = None) -> Path | dict[str, Any]:
    value = call.args.get(key)
    if not isinstance(value, str) or not value.strip():
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"missing required file argument: {key}"}
    path = _repo_path(value)
    if path is None:
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"{key} must be inside repository"}
    if suffix and path.suffix.lower() != suffix:
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"{key} must end with {suffix}"}
    if not path.exists() or not path.is_file():
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"file not found: {path}"}
    return path


def _resolve_inp_for_run(call: ToolCall) -> Path | dict[str, Any]:
    raw = str(call.args.get("inp_path", "")).strip()
    if not raw:
        return {"tool": call.name, "args": call.args, "ok": False, "summary": "missing required file argument: inp_path"}

    repo_file = _required_repo_file(call, "inp_path", suffix=".inp")
    if not isinstance(repo_file, dict):
        return repo_file

    resolved = _find_repo_inp(raw)
    if resolved is not None:
        return resolved

    external = Path(raw).expanduser()
    try:
        external = external.resolve()
    except OSError:
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"inp_path could not be resolved: {raw}"}
    if external.suffix.lower() != ".inp":
        return {"tool": call.name, "args": call.args, "ok": False, "summary": "inp_path must end with .inp"}
    if not external.exists() or not external.is_file():
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"external INP file not found: {external}"}
    return external


def _find_repo_inp(value: str) -> Path | None:
    if not value or Path(value).is_absolute() or "/" in value:
        return None
    root = repo_root() / "examples"
    if not root.exists():
        return None
    matches = sorted(path for path in root.rglob(value) if path.is_file() and path.suffix.lower() == ".inp")
    return matches[0] if matches else None


def _required_repo_dir(call: ToolCall, key: str) -> Path | dict[str, Any]:
    value = call.args.get(key)
    if not isinstance(value, str) or not value.strip():
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"missing required directory argument: {key}"}
    path = _repo_path(value)
    if path is None:
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"{key} must be inside repository"}
    if not path.exists() or not path.is_dir():
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"directory not found: {path}"}
    return path


def _optional_repo_output_dir(call: ToolCall, key: str) -> Path | dict[str, Any] | None:
    value = call.args.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"{key} must be a non-empty string"}
    path = _repo_path(value)
    if path is None:
        return {"tool": call.name, "args": call.args, "ok": False, "summary": f"{key} must be inside repository"}
    path.mkdir(parents=True, exist_ok=True)
    return path


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
