#!/usr/bin/env python3
"""Validate that a skill folder is well-formed before it is proposed for review.

Domain-general: this knows nothing about SWMM or any specific domain. It checks
the structural contract every skill must satisfy — a SKILL.md carrying a name +
description in its frontmatter and a non-empty body — so a freshly drafted skill
can be auto-checked before a human is asked to approve it.

Usage:
    python3 validate_skill.py path/to/skill-dir

Exit code 0 = valid, 1 = problems (printed to stderr), 2 = bad invocation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MIN_DESCRIPTION_CHARS = 40
_BLOCK_SCALAR_RE = re.compile(r"^[>|][+-]?$")


def _parse_frontmatter(text):
    """Split leading ``---`` YAML frontmatter from the body.

    Returns ``(front, body)`` where ``front`` is a dict of the simple
    ``key: value`` scalars (all a skill header needs), or ``(None, text)`` if
    there is no frontmatter block. Values written as YAML block scalars
    (``key: >`` / ``key: |`` with indented continuation lines) are joined so
    a multi-line description measures its real length, and indented
    continuation lines are never misread as keys.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            front = {}
            j = 1
            while j < i:
                raw = lines[j]
                if raw.startswith((" ", "\t")) or ":" not in raw:
                    j += 1
                    continue
                key, _, val = raw.partition(":")
                val = val.strip()
                if _BLOCK_SCALAR_RE.match(val):
                    # Folded (>) joins with spaces; literal (|) keeps newlines.
                    sep = " " if val.startswith(">") else "\n"
                    parts = []
                    j += 1
                    while j < i and (
                        lines[j].startswith((" ", "\t")) or not lines[j].strip()
                    ):
                        parts.append(lines[j].strip())
                        j += 1
                    front[key.strip()] = sep.join(p for p in parts if p)
                    continue
                front[key.strip()] = val
                j += 1
            return front, "\n".join(lines[i + 1:])
    return None, text


def validate(skill_dir):
    """Check one skill folder. Return a list of human-readable problems (empty = OK)."""
    skill_dir = Path(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"no SKILL.md found in {skill_dir}"]

    front, body = _parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="ignore"))
    if front is None:
        return ["SKILL.md has no '---' YAML frontmatter block at the top"]

    problems = []

    name = front.get("name", "")
    if not name:
        problems.append("frontmatter is missing a 'name'")
    else:
        if not _NAME_RE.match(name):
            problems.append(f"name '{name}' is not kebab-case (lowercase words joined by '-')")
        if name != skill_dir.name:
            problems.append(f"name '{name}' does not match the folder name '{skill_dir.name}'")

    description = front.get("description", "")
    if not description:
        problems.append(
            "frontmatter is missing a 'description' (this is how the agent decides when to use the skill)"
        )
    elif len(description) < _MIN_DESCRIPTION_CHARS:
        problems.append(
            f"description is only {len(description)} chars; write a fuller one that says "
            "what it does AND when to use it"
        )

    if not body.strip():
        problems.append("SKILL.md has an empty body below the frontmatter")

    return problems


def main(argv):
    if len(argv) != 2:
        print("usage: python3 validate_skill.py path/to/skill-dir", file=sys.stderr)
        return 2
    problems = validate(argv[1])
    if problems:
        print(f"INVALID: {argv[1]}", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"PASS: {argv[1]} is a well-formed skill")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
