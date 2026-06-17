"""Lessons-compaction trigger for modeling memory (PRD M3).

When ``lessons_learned.md`` grows past a size or pattern-count threshold,
``maybe_prepend_compaction_marker`` prepends an ``<!-- LLM-TODO -->`` marker so
a follow-up pass can compact it.

The skill-update-proposal renderer that used to live here was retired once the
live generator — ``skills/swmm-modeling-memory/scripts/summarize_memory.py`` —
became the single source of ``skill_update_proposals.md`` (it also proposes a
new skill, via skill-author, for any pattern no existing skill recognises).
"""

from __future__ import annotations

import re
from pathlib import Path


_COMPACTION_MARKER = "<!-- LLM-TODO: compact-lessons"
_LESSONS_SIZE_THRESHOLD_BYTES = 50 * 1024
_LESSONS_PATTERN_COUNT_THRESHOLD = 40
_H2_RE = re.compile(r"^##\s+(?!Repeated|Successful|Run-to-Run)([A-Za-z0-9_.\-]+)\s*$", flags=re.MULTILINE)


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
