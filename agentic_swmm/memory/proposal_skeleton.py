"""Skill-update proposal skeleton + lessons-compaction trigger (PRD M3).

Both functions are pure (modulo filesystem reads / writes) and emit
``<!-- LLM-TODO -->`` markers so a follow-up LLM pass can supply the
concrete diff and the compaction edits without the structure changing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Sequence


_COMPACTION_MARKER = "<!-- LLM-TODO: compact-lessons"
_LESSONS_SIZE_THRESHOLD_BYTES = 50 * 1024
_LESSONS_PATTERN_COUNT_THRESHOLD = 40
_H2_RE = re.compile(r"^##\s+(?!Repeated|Successful|Run-to-Run)([A-Za-z0-9_.\-]+)\s*$", flags=re.MULTILINE)


def _evidence_link(note: Path) -> str:
    """Render an Obsidian-style wikilink for an audit note.

    The wikilink target uses the parent-of-09_audit directory stem,
    matching the convention used by Audit PRD's MOC generator.
    """
    parts = list(note.parts)
    if "09_audit" in parts:
        idx = parts.index("09_audit")
        case_name = parts[idx - 1] if idx > 0 else note.stem
    else:
        case_name = note.parent.name or note.stem
    return f"[[{case_name}]]"


def _skill_mentions_pattern(skill_file: Path, pattern: str) -> bool:
    if not skill_file.is_file():
        return False
    try:
        text = skill_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return pattern in text


def build_proposal(
    pattern: str,
    audit_notes: Sequence[Path],
    skill_files: Sequence[Path],
) -> str:
    """Render the skeleton proposal for a single ``failure_pattern``.

    Parameters
    ----------
    pattern:
        ``failure_pattern`` name (used both as the H2 heading and as
        the literal grep target when scoring skill relevance).
    audit_notes:
        Audit-note paths that documented this pattern. Each becomes an
        Obsidian wikilink in the Evidence runs section.
    skill_files:
        Skill ``SKILL.md`` paths to consider. The Relevant skills
        section lists only those whose text mentions ``pattern``.
    """
    relevant = [skill for skill in skill_files if _skill_mentions_pattern(skill, pattern)]

    skills_block = (
        "\n".join(f"- `{skill.parent.name}` ({skill})" for skill in relevant)
        if relevant
        else "- _(no skill SKILL.md mentions this pattern; manual triage required)_"
    )
    evidence_block = (
        "\n".join(f"- {_evidence_link(note)}" for note in audit_notes)
        if audit_notes
        else "- _(no audit notes recorded for this pattern yet)_"
    )

    proposed_change = (
        "<!-- LLM-TODO: produce a concrete diff against the relevant "
        "SKILL.md based on the evidence runs. Trigger via: "
        f"aiswmm memory --propose --pattern={pattern} -->"
    )
    required_control = (
        "All skill updates require human review and benchmark "
        "verification before merge. This is a proposal, not an auto-applied edit."
    )

    return (
        f"## {pattern}\n\n"
        "### Relevant skills\n"
        f"{skills_block}\n\n"
        "### Evidence runs\n"
        f"{evidence_block}\n\n"
        "### Proposed change\n"
        f"{proposed_change}\n\n"
        "### Required control\n"
        f"{required_control}\n"
    )


def _count_failure_pattern_sections(text: str) -> int:
    return len(_H2_RE.findall(text))


def maybe_prepend_compaction_marker(lessons_path: Path) -> bool:
    """Prepend the compaction marker if the lessons file exceeds thresholds.

    Threshold (PRD M3): file size > 50 KB **or** distinct failure-pattern
    ``## <pattern>`` sections > 40. Returns ``True`` if the marker was
    added on this call, ``False`` if the file was already marked or
    under threshold.
    """
    if not lessons_path.is_file():
        return False
    text = lessons_path.read_text(encoding="utf-8")
    if _COMPACTION_MARKER in text:
        return False
    size = len(text.encode("utf-8"))
    pattern_count = _count_failure_pattern_sections(text)
    too_big = size > _LESSONS_SIZE_THRESHOLD_BYTES
    too_many = pattern_count > _LESSONS_PATTERN_COUNT_THRESHOLD
    if not (too_big or too_many):
        return False
    marker = (
        f"{_COMPACTION_MARKER}. File is {size // 1024} KB / "
        f"{pattern_count} patterns. Review stale or merged patterns "
        "and propose a compaction diff. Trigger via: "
        "aiswmm memory --compact -->\n\n"
    )
    lessons_path.write_text(marker + text, encoding="utf-8")
    return True


def build_proposal_document(
    pattern_evidence: dict[str, Iterable[Path]],
    skill_files: Sequence[Path],
) -> str:
    """Render a multi-pattern proposal document.

    ``pattern_evidence`` maps each ``failure_pattern`` to the audit
    notes that exhibit it.
    """
    sections = [
        build_proposal(pattern, list(notes), skill_files)
        for pattern, notes in sorted(pattern_evidence.items())
    ]
    return "# Skill update proposals\n\n" + "\n".join(sections)
