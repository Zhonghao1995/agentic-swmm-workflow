from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.utils.paths import repo_root


def agent_say(text: str) -> None:
    if not text:
        print("agent>")
        return
    lines = text.splitlines() or [text]
    for line in lines:
        print(f"agent> {line}" if line else "agent>")


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root().resolve()))
    except ValueError:
        return str(path)


def compact_plan(plan: list[Any]) -> str:
    if not plan:
        return "no tool calls"
    return " -> ".join(str(call.name) for call in plan)
