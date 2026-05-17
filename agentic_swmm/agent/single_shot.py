"""Non-interactive single-shot executor for the ``aiswmm agent`` CLI.

This module owns the ``--planner rule`` single-shot flow used by tests
and by ``aiswmm agent <goal>`` without ``--interactive``. The live
tool-dispatch implementation lives in
``agentic_swmm/agent/tool_registry.py``; this module only assembles a
rule-planner plan and hands it to ``run_rule_plan``.

The two module-level helpers (``_find_repo_inp`` and ``_safe_name``) are
kept at the top of the file because they have external callers
(``tests/test_agentic_swmm_cli.py`` and ``agent/runtime_loop.py``).
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.mcp_pool import ensure_session_pool
from agentic_swmm.agent.planner import rule_plan
from agentic_swmm.agent.reporting import write_event as _write_event
from agentic_swmm.agent.reporting import write_report as _write_report
from agentic_swmm.agent.runtime import run_rule_plan
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.ui import agent_say as _agent_say
from agentic_swmm.agent.ui import compact_plan as _compact_plan
from agentic_swmm.agent.ui import display_path as _display_path
from agentic_swmm.utils.paths import repo_root


def _safe_name(value: str) -> str:
    """Normalise an arbitrary string into a filesystem-safe slug."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "agent"


def _find_repo_inp(value: str) -> Path | None:
    """Resolve a bare ``.inp`` filename to a path under ``examples/``."""
    if not value or Path(value).is_absolute() or "/" in value:
        return None
    root = repo_root() / "examples"
    if not root.exists():
        return None
    matches = sorted(
        path
        for path in root.rglob(value)
        if path.is_file() and path.suffix.lower() == ".inp"
    )
    return matches[0] if matches else None


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
    # PRD-X: bind a per-process MCP pool so list_mcp_tools / call_mcp_tool
    # reuse one long-running node child per server instead of paying
    # cold-start cost every call. Lazy — pool only spawns on first use.
    ensure_session_pool()
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

    # Late import keeps the agent runtime free of a CLI-layer dependency
    # in the import graph (commands/agent.py imports single_shot).
    from agentic_swmm.commands.agent import resolve_profile_from_args

    profile = resolve_profile_from_args(args)
    executor = AgentExecutor(
        registry,
        session_dir=session_dir,
        trace_path=trace_path,
        dry_run=False,
        profile=profile,
    )
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
    # Issue #60 (UX-5): mirror runtime_loop's session-end MOC refresh so
    # the non-interactive single-shot path keeps runs/INDEX.md fresh too.
    # Late import keeps this file free of an audit-layer dep at import time.
    from agentic_swmm.agent.runtime_loop import _refresh_moc_after_session

    _refresh_moc_after_session(session_dir)
    _agent_say(f"Final report: {_display_path(report)}")
    return 0 if outcome.ok else 1
