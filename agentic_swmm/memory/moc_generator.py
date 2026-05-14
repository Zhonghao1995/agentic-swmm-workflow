"""Memory MOC generator (PRD M4).

Reads ``modeling_memory_index.json`` (audit -> failure_patterns +
skills_used) and ``skill_update_proposals.md`` (pattern -> proposed
skill targets), and emits ``memory/modeling-memory/INDEX.md`` with two
tables:

1. ``By failure pattern``: pattern, count, wikilinks to every audit
   note that exhibited it.
2. ``By skill impact``: each ``SKILL.md`` mentioned in proposals,
   listing the patterns proposing changes to it.

Mirrors the audit MOC (``agentic_swmm/audit/moc_generator.py``) so an
Obsidian user navigating between the two MOCs sees one consistent
layout.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_PROPOSAL_PATTERN_RE = re.compile(r"^##\s+`?([A-Za-z0-9_.\-]+)`?\s*$", flags=re.MULTILINE)
_PROPOSAL_SKILL_BLOCK_RE = re.compile(
    r"^##\s+`?([A-Za-z0-9_.\-]+)`?\s*$([\s\S]*?)(?=^##\s+|\Z)",
    flags=re.MULTILINE,
)
_BACKTICK_SKILL_RE = re.compile(r"`([A-Za-z0-9_./\-]+)`")


def _read_records(memory_dir: Path) -> list[dict[str, Any]]:
    path = memory_dir / "modeling_memory_index.json"
    if not path.is_file():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, dict):
        return []
    records = parsed.get("records") or []
    return [record for record in records if isinstance(record, dict)]


def _audit_note_link(runs_dir: Path, run_id: str) -> str:
    """Render an Obsidian wikilink to an audit note.

    Looks for ``runs/<run_id>/09_audit/experiment_note.md``; if the
    note does not exist, the link still uses ``run_id`` as the target
    so the MOC degrades gracefully.
    """
    return f"[[{run_id}]]"


def _by_failure_pattern(records: list[dict[str, Any]], runs_dir: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for record in records:
        run_id = record.get("run_id")
        for pattern in record.get("failure_patterns") or []:
            if not run_id or not pattern:
                continue
            out[str(pattern)].append(_audit_note_link(runs_dir, str(run_id)))
    return out


def _by_skill_impact(
    records: list[dict[str, Any]],
    proposal_text: str,
) -> dict[str, list[str]]:
    """Map each SKILL.md mentioned in proposals -> patterns proposing changes."""
    out: dict[str, set[str]] = defaultdict(set)

    for match in _PROPOSAL_SKILL_BLOCK_RE.finditer(proposal_text):
        pattern = match.group(1)
        body = match.group(2)
        skills_in_block = _BACKTICK_SKILL_RE.findall(body)
        for skill in skills_in_block:
            if not skill.startswith("swmm-"):
                continue
            out[skill].add(pattern)

    # Fallback: records may already carry skills_used pointing at SKILL.md.
    for record in records:
        for skill in record.get("skills_used") or []:
            for pattern in record.get("failure_patterns") or []:
                out[str(skill)].add(str(pattern))

    return {skill: sorted(patterns) for skill, patterns in out.items()}


def _render_table(rows: list[tuple[str, ...]], headers: tuple[str, ...]) -> str:
    if not rows:
        return f"| {' | '.join(headers)} |\n| {' | '.join('---' for _ in headers)} |\n| _(none)_ |\n"
    header_line = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(cells) + " |" for cells in rows)
    return f"{header_line}\n{sep}\n{body}\n"


def generate_memory_moc(memory_dir: Path, runs_dir: Path) -> str:
    """Render the memory MOC as a Markdown string."""
    records = _read_records(memory_dir)
    proposals_text = ""
    proposals_path = memory_dir / "skill_update_proposals.md"
    if proposals_path.is_file():
        try:
            proposals_text = proposals_path.read_text(encoding="utf-8")
        except OSError:
            proposals_text = ""

    by_pattern = _by_failure_pattern(records, runs_dir)
    by_skill = _by_skill_impact(records, proposals_text)

    pattern_rows = [
        (pattern, str(len(links)), " ".join(sorted(set(links))))
        for pattern, links in sorted(by_pattern.items())
    ]
    skill_rows = [
        (skill, str(len(patterns)), " ".join(f"`{p}`" for p in patterns))
        for skill, patterns in sorted(by_skill.items())
    ]

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        "---\n"
        "type: memory-index\n"
        f"generated_at_utc: {generated_at}\n"
        "---\n\n"
        "# Modeling memory MOC\n\n"
        "Navigation index over the audited history that informed "
        "`lessons_learned.md` and `skill_update_proposals.md`. Wikilinks "
        "point at the underlying audit notes.\n\n"
        "## By failure pattern\n\n"
        f"{_render_table(pattern_rows, ('failure_pattern', 'runs', 'audit notes'))}\n"
        "## By skill impact\n\n"
        f"{_render_table(skill_rows, ('skill', 'pattern count', 'patterns'))}"
    )


def write_memory_moc(memory_dir: Path, runs_dir: Path) -> Path:
    """Generate and write ``INDEX.md`` under ``memory_dir``."""
    text = generate_memory_moc(memory_dir, runs_dir)
    out = memory_dir / "INDEX.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out
