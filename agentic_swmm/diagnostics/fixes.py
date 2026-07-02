"""``aiswmm doctor --fix`` remediation actions.

Collect the safe remediations a doctor report justifies, then apply
them with an interactive confirm loop. Split from the report data
layer (``diagnostics.doctor_report``) because applying is genuinely
side-effectful — it prompts on stdin and shells out — while everything
in the report module returns data. The streams and the subprocess
runner are dependency-injected so tests drive the loop with StringIO
and a recording stub.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import IO, Any

from agentic_swmm.diagnostics.doctor_report import (
    GroupedWarnRow,
    MemoryStoreStatus,
)


@dataclass(frozen=True)
class FixAction:
    """One remediation doctor can run on the user's behalf."""

    label: str
    command: list[str]
    triggers: list[str]
    interactive_confirm: bool = True


def collect_fix_actions(doctor_report: dict) -> list[FixAction]:
    """Walk the doctor report; return safe remediations doctor can apply.

    Currently supports:

    * MCP-drift collapse → ``aiswmm setup --refresh-mcp``
    * Missing memory stores → ``aiswmm bootstrap memory``

    The report shape (see :func:`commands.doctor.main`) is::

        {
            "checks": [{"name": ..., "passed": bool, "detail": str, "required": bool}, ...],
            "memory_stores": [MemoryStoreStatus, ...],
            "optout_status": [OptOutFlagStatus, ...],
            "grouped_warns": [GroupedWarnRow | dict, ...],
        }
    """
    actions: list[FixAction] = []

    # ---- MCP drift?
    drifted_servers: list[str] = []
    for row in doctor_report.get("grouped_warns", []):
        if isinstance(row, GroupedWarnRow):
            drifted_servers.extend(row.member_names)
    if not drifted_servers:
        # Fallback: scan raw checks for the drift detail substring.
        for check in doctor_report.get("checks", []):
            detail = check.get("detail", "")
            if (
                "mcp.json routes" in detail
                and "different checkout" in detail
            ):
                drifted_servers.append(str(check.get("name", "")))
    if drifted_servers:
        actions.append(
            FixAction(
                label="Refresh mcp.json to current install",
                command=["aiswmm", "setup", "--refresh-mcp"],
                triggers=drifted_servers,
                interactive_confirm=True,
            )
        )

    # ---- Missing memory stores?
    missing_stores = [
        s
        for s in doctor_report.get("memory_stores", [])
        if isinstance(s, MemoryStoreStatus) and s.severity == "MISSING"
    ]
    # Only offer bootstrap when at least one of the four core JSONL/MD
    # stores is missing — the YAML libraries ship with the package and
    # don't need bootstrapping.
    bootstrap_candidates = {
        "parametric_memory.jsonl",
        "calibration_memory.jsonl",
        "negative_lessons.jsonl",
        "negative_lessons.md",
    }
    missing_bootstrap = [s for s in missing_stores if s.name in bootstrap_candidates]
    if missing_bootstrap:
        actions.append(
            FixAction(
                label="Create missing memory stores",
                command=["aiswmm", "bootstrap", "memory"],
                triggers=[s.name for s in missing_bootstrap],
                interactive_confirm=True,
            )
        )

    return actions


def apply_fix_actions(
    actions: list[FixAction],
    *,
    yes: bool = False,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    subprocess_runner: Any = None,
) -> dict[str, str]:
    """Apply each :class:`FixAction`, prompting unless ``yes=True``.

    Returns a dict ``{action.label: "applied" | "skipped" | "failed"}``.

    ``stdin``/``stdout`` default to the real streams; tests pass
    StringIO objects. ``subprocess_runner`` defaults to
    :func:`subprocess.run`; tests pass a stub recording the command.
    """
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout
    if subprocess_runner is None:
        subprocess_runner = subprocess.run

    results: dict[str, str] = {}
    for action in actions:
        prompt = (
            f"\n* {action.label}\n"
            f"  Command: {' '.join(action.command)}\n"
            f"  Triggered by: {', '.join(action.triggers)}\n"
        )
        stdout.write(prompt)
        if action.interactive_confirm and not yes:
            stdout.write("  Apply now? [y/N] ")
            stdout.flush()
            response = stdin.readline().strip().lower()
            if response not in {"y", "yes"}:
                results[action.label] = "skipped"
                stdout.write("  skipped.\n")
                continue
        try:
            proc = subprocess_runner(
                action.command,
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception as exc:  # pragma: no cover - subprocess plumbing
            results[action.label] = "failed"
            stdout.write(f"  failed: {exc}\n")
            continue
        rc = getattr(proc, "returncode", 0)
        if rc == 0:
            results[action.label] = "applied"
            stdout.write("  applied.\n")
        else:
            results[action.label] = "failed"
            stdout.write(f"  failed (exit {rc}).\n")
    return results


def fix_action_to_dict(action: FixAction) -> dict:
    return {
        "label": action.label,
        "command": list(action.command),
        "triggers": list(action.triggers),
        "interactive_confirm": action.interactive_confirm,
    }


__all__ = [
    "FixAction",
    "apply_fix_actions",
    "collect_fix_actions",
    "fix_action_to_dict",
]
