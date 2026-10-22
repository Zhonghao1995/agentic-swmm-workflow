"""Tool notes: promoted tool-level experience that rides the tool description.

Skill refinement loop (ADR-0014, #562). ``memory/initial/tool_notes.md``
ships with the package, hand-reviewed, one ``## <tool>`` section per
tool with at most :data:`MAX_NOTES_PER_TOOL` bullets of
:data:`MAX_NOTE_CHARS` characters. When the tool registry is built, each
tool's notes are appended to its description, so the planner reads them
at the moment it considers the tool, every turn, without touching the
startup memory budget and without depending on a ``read_skill`` call.

The program never writes the file: a note gets there through a promoted
proposal of kind ``tool`` (``aiswmm memory promote``) or a pull request.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

TOOL_NOTES_FILE = "memory/initial/tool_notes.md"
MAX_NOTES_PER_TOOL = 3
MAX_NOTE_CHARS = 200
#: The line that introduces the notes inside a tool description.
NOTES_LEAD = "Learned from runs:"

_SECTION_RE = re.compile(r"^## (\S+)\s*$")
_cache: dict[str, Any] = {}


def tool_notes_path() -> Path:
    """The shipped notes file (``AISWMM_TOOL_NOTES`` overrides it, for tests)."""
    override = os.environ.get("AISWMM_TOOL_NOTES")
    if override:
        return Path(override)
    from agentic_swmm.utils.paths import initial_memory_dir

    return initial_memory_dir() / "tool_notes.md"


def parse_tool_notes(text: str) -> dict[str, list[str]]:
    """``{tool: [note, ...]}`` from the file's ``## <tool>`` sections and their bullets."""
    notes: dict[str, list[str]] = {}
    tool: str | None = None
    for line in text.splitlines():
        heading = _SECTION_RE.match(line)
        if heading:
            tool = heading.group(1)
            notes.setdefault(tool, [])
            continue
        if tool and line.startswith("- "):
            notes[tool].append(line[2:].strip())
    return {name: items for name, items in notes.items() if items}


def load_tool_notes(path: Path | None = None) -> dict[str, list[str]]:
    """The notes, read once per file version (size and mtime) per process."""
    path = Path(path) if path is not None else tool_notes_path()
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if _cache.get("key") == key:
        return _cache["notes"]
    try:
        notes = parse_tool_notes(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        notes = {}
    _cache["key"], _cache["notes"] = key, notes
    return notes


def clear_cache() -> None:
    _cache.clear()


def describe_with_notes(description: str, notes: list[str]) -> str:
    """The tool description with its notes appended, each capped."""
    kept = [note[:MAX_NOTE_CHARS] for note in notes[:MAX_NOTES_PER_TOOL]]
    if not kept:
        return description
    return description.rstrip() + "\n" + NOTES_LEAD + " " + " ".join(f"({n}) {note}" for n, note in enumerate(kept, 1))


def apply_tool_notes(tools: dict[str, Any], notes: dict[str, list[str]] | None = None) -> dict[str, int]:
    """Append each tool's notes to its spec in ``tools``; return ``{tool: chars added}``."""
    notes = load_tool_notes() if notes is None else notes
    added: dict[str, int] = {}
    for name, items in notes.items():
        spec = tools.get(name)
        if spec is None or not items:
            continue
        text = describe_with_notes(spec.description, items)
        if text != spec.description:
            tools[name] = replace(spec, description=text)
            added[name] = len(text) - len(spec.description)
    return added


def fit_note(text: str, cap: int = MAX_NOTE_CHARS) -> str:
    """A bullet that fits the note cap: whole, or cut at a word with ``...``."""
    body = text.strip()
    if len(body) <= cap:
        return body
    cut = body[: cap - 3]
    if " " in cut[cap // 2 :]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip() + "..."
