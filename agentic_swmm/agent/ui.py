from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent import ui_colors
from agentic_swmm.utils.paths import repo_root


_PROMPT = "aiswmm>"
_INDENT = " " * (len(_PROMPT) + 1)  # match "aiswmm> " spacing


def agent_say(text: str) -> None:
    """Print an agent line. Only the first non-empty line gets the
    ``aiswmm>`` prefix; subsequent lines are indented to align with it
    (PRD_runtime user story 7).
    """
    if not text:
        print(_styled_prompt())
        return
    lines = text.splitlines() or [text]
    first = True
    for line in lines:
        if not line:
            # Preserve blank lines as a bare indent so paragraphs hold
            # together visually.
            print(_INDENT.rstrip())
            continue
        if first:
            print(f"{_styled_prompt()} {line}")
            first = False
        else:
            print(f"{_INDENT}{line}")


def _styled_prompt() -> str:
    return ui_colors.colorize(_PROMPT, ui_colors.FG_BLUE)


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root().resolve()))
    except ValueError:
        return str(path)


def compact_plan(plan: list[Any]) -> str:
    if not plan:
        return "no tool calls"
    return " -> ".join(str(call.name) for call in plan)
