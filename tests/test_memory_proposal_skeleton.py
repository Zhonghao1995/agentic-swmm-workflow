"""Unit tests for ``agentic_swmm.memory.proposal_skeleton`` — the lessons-compaction trigger (PRD M3)."""

from __future__ import annotations

from pathlib import Path


def test_compaction_marker_added_when_lessons_exceeds_size(tmp_path: Path) -> None:
    from agentic_swmm.memory.proposal_skeleton import maybe_prepend_compaction_marker

    big = "x" * (60 * 1024)  # 60 KB
    lessons_path = tmp_path / "lessons_learned.md"
    lessons_path.write_text(f"# Lessons\n\n{big}", encoding="utf-8")
    changed = maybe_prepend_compaction_marker(lessons_path)
    assert changed is True
    out = lessons_path.read_text(encoding="utf-8")
    assert "<!-- LLM-TODO: compact-lessons" in out
    # And the original header must still be there.
    assert "# Lessons" in out


def test_compaction_marker_not_added_when_below_threshold(tmp_path: Path) -> None:
    from agentic_swmm.memory.proposal_skeleton import maybe_prepend_compaction_marker

    small = "small content\n" * 100  # << 50 KB
    lessons_path = tmp_path / "lessons_learned.md"
    lessons_path.write_text(small, encoding="utf-8")
    changed = maybe_prepend_compaction_marker(lessons_path)
    assert changed is False
    assert "compact-lessons" not in lessons_path.read_text(encoding="utf-8")


def test_compaction_marker_triggers_on_pattern_count_threshold(tmp_path: Path) -> None:
    from agentic_swmm.memory.proposal_skeleton import maybe_prepend_compaction_marker

    sections = "\n".join(f"## pattern_{i}\n\nbody.\n" for i in range(45))
    lessons_path = tmp_path / "lessons_learned.md"
    lessons_path.write_text("# Lessons\n" + sections, encoding="utf-8")
    changed = maybe_prepend_compaction_marker(lessons_path)
    assert changed is True
    assert "compact-lessons" in lessons_path.read_text(encoding="utf-8")


def test_compaction_marker_not_duplicated_on_subsequent_calls(tmp_path: Path) -> None:
    from agentic_swmm.memory.proposal_skeleton import maybe_prepend_compaction_marker

    big = "x" * (60 * 1024)
    lessons_path = tmp_path / "lessons_learned.md"
    lessons_path.write_text(f"# Lessons\n\n{big}", encoding="utf-8")
    assert maybe_prepend_compaction_marker(lessons_path) is True
    assert maybe_prepend_compaction_marker(lessons_path) is False
    occurrences = lessons_path.read_text(encoding="utf-8").count("compact-lessons")
    assert occurrences == 1
