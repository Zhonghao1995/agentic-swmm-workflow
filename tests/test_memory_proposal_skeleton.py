"""Unit tests for ``agentic_swmm.memory.proposal_skeleton`` (PRD M3)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_build_proposal_links_all_evidence_runs(tmp_path: Path) -> None:
    from agentic_swmm.memory.proposal_skeleton import build_proposal

    audit_notes = [
        tmp_path / "runs" / "case-a" / "09_audit" / "experiment_note.md",
        tmp_path / "runs" / "case-b" / "09_audit" / "experiment_note.md",
        tmp_path / "runs" / "case-c" / "09_audit" / "experiment_note.md",
    ]
    for note in audit_notes:
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# audit note\n", encoding="utf-8")
    skill_files = [
        tmp_path / "skills" / "swmm-runner" / "SKILL.md",
        tmp_path / "skills" / "swmm-experiment-audit" / "SKILL.md",
    ]
    for skill in skill_files:
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text("# skill\nMentions peak_flow_parse_missing.\n", encoding="utf-8")

    out = build_proposal(
        "peak_flow_parse_missing",
        audit_notes=audit_notes,
        skill_files=skill_files,
    )
    assert "## peak_flow_parse_missing" in out
    # All three evidence runs linked as Obsidian wikilinks.
    for note in audit_notes:
        assert note.stem in out or note.parent.parent.name in out
    # LLM-TODO marker for the diff is present.
    assert "<!-- LLM-TODO" in out
    # Relevant skills section enumerates both heuristic matches.
    assert "swmm-runner" in out
    assert "swmm-experiment-audit" in out
    # The Required Control / human review boilerplate is present.
    assert "human review" in out.lower() or "human reviewer" in out.lower()


def test_build_proposal_handles_zero_evidence_runs(tmp_path: Path) -> None:
    from agentic_swmm.memory.proposal_skeleton import build_proposal

    out = build_proposal(
        "some_pattern",
        audit_notes=[],
        skill_files=[],
    )
    assert "## some_pattern" in out
    assert "<!-- LLM-TODO" in out
    # No evidence section bullets — but the section header must still exist.
    assert "Evidence runs" in out


def test_build_proposal_relevant_skills_heuristic(tmp_path: Path) -> None:
    from agentic_swmm.memory.proposal_skeleton import build_proposal

    relevant = tmp_path / "skills" / "swmm-runner" / "SKILL.md"
    relevant.parent.mkdir(parents=True)
    relevant.write_text("Talks about peak_flow_parse_missing.\n", encoding="utf-8")

    unrelated = tmp_path / "skills" / "swmm-climate" / "SKILL.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("Only rainfall things.\n", encoding="utf-8")

    out = build_proposal(
        "peak_flow_parse_missing",
        audit_notes=[],
        skill_files=[relevant, unrelated],
    )
    assert "swmm-runner" in out
    # The unrelated skill should not appear in the Relevant skills bullets.
    relevant_block = out.split("Relevant skills", 1)[1].split("Evidence runs", 1)[0]
    assert "swmm-climate" not in relevant_block


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
