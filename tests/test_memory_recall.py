"""Unit tests for ``agentic_swmm.memory.recall`` (PRD M1)."""

from __future__ import annotations

from pathlib import Path

import pytest


LESSONS_SAMPLE = """# Lessons Learned

Generated at UTC: `2026-05-13T00:00:00+00:00`

## Repeated Failure Patterns
- `peak_flow_parse_missing`: 3 run(s)
- `continuity_parse_missing`: 5 run(s)

## peak_flow_parse_missing

This failure occurs when the runner cannot locate the peak flow value
in the parsed RPT output. Common cause: outfall node naming mismatch.

Lesson: always verify the node argument resolves to a known [OUTFALLS]
entry before running.

## continuity_parse_missing

The continuity error line is absent from the RPT. Often caused by an
incomplete run that exited early.

## edge.case-name

A pattern name with a dot and a dash. Should be looked up safely.

## end
"""


def _write_lessons(tmp_path: Path, content: str = LESSONS_SAMPLE) -> Path:
    path = tmp_path / "lessons_learned.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_recall_returns_section_when_pattern_exists(tmp_path: Path) -> None:
    from agentic_swmm.memory.recall import recall

    lessons = _write_lessons(tmp_path)
    out = recall("peak_flow_parse_missing", lessons)

    assert out, "expected a non-empty section for an existing pattern"
    assert "peak_flow_parse_missing" in out
    assert "outfall node naming mismatch" in out
    # Must not bleed into the next section.
    assert "continuity_parse_missing" not in out
    assert "edge.case-name" not in out


def test_recall_returns_empty_when_pattern_absent(tmp_path: Path) -> None:
    from agentic_swmm.memory.recall import recall

    lessons = _write_lessons(tmp_path)
    assert recall("not_a_real_pattern", lessons) == ""


def test_recall_returns_empty_when_lessons_file_missing(tmp_path: Path) -> None:
    from agentic_swmm.memory.recall import recall

    missing = tmp_path / "does_not_exist.md"
    assert recall("peak_flow_parse_missing", missing) == ""


def test_recall_handles_markdown_special_chars_in_pattern(tmp_path: Path) -> None:
    from agentic_swmm.memory.recall import recall

    lessons = _write_lessons(tmp_path)
    out = recall("edge.case-name", lessons)
    assert "pattern name with a dot and a dash" in out


def test_recall_section_stops_before_next_h2(tmp_path: Path) -> None:
    from agentic_swmm.memory.recall import recall

    lessons = _write_lessons(tmp_path)
    out = recall("peak_flow_parse_missing", lessons)
    # The "## end" heading itself should not be included.
    assert "## end" not in out
