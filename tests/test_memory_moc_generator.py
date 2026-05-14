"""Unit tests for ``agentic_swmm.memory.moc_generator`` (PRD M4)."""

from __future__ import annotations

import json
from pathlib import Path


def _seed_memory_index(memory_dir: Path) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "modeling_memory_index.json").write_text(
        json.dumps(
            {
                "schema_version": "1.1",
                "records": [
                    {
                        "run_id": "case-a",
                        "case_name": "Case A",
                        "failure_patterns": ["peak_flow_parse_missing"],
                        "skills_used": ["swmm-runner"],
                    },
                    {
                        "run_id": "case-b",
                        "case_name": "Case B",
                        "failure_patterns": ["peak_flow_parse_missing", "continuity_parse_missing"],
                        "skills_used": ["swmm-runner", "swmm-experiment-audit"],
                    },
                    {
                        "run_id": "case-c",
                        "case_name": "Case C",
                        "failure_patterns": ["continuity_parse_missing"],
                        "skills_used": ["swmm-experiment-audit"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (memory_dir / "skill_update_proposals.md").write_text(
        "# Skill update proposals\n\n"
        "## peak_flow_parse_missing\n"
        "### Relevant skills\n"
        "- `swmm-runner`\n"
        "## continuity_parse_missing\n"
        "### Relevant skills\n"
        "- `swmm-experiment-audit`\n",
        encoding="utf-8",
    )


def _seed_audit_notes(runs_dir: Path, names: list[str]) -> None:
    for name in names:
        audit_dir = runs_dir / name / "09_audit"
        audit_dir.mkdir(parents=True)
        (audit_dir / "experiment_note.md").write_text("# note\n", encoding="utf-8")


def test_generate_memory_moc_has_both_required_tables(tmp_path: Path) -> None:
    from agentic_swmm.memory.moc_generator import generate_memory_moc

    memory_dir = tmp_path / "memory" / "modeling-memory"
    runs_dir = tmp_path / "runs"
    _seed_memory_index(memory_dir)
    _seed_audit_notes(runs_dir, ["case-a", "case-b", "case-c"])

    out = generate_memory_moc(memory_dir, runs_dir)
    # Frontmatter announces this as a memory MOC.
    assert "---" in out
    assert "type: memory-index" in out
    # Both required tables present (per PRD M4 + Done Criteria).
    assert "By failure pattern" in out
    assert "By skill impact" in out


def test_generate_memory_moc_links_each_audit_note_per_pattern(tmp_path: Path) -> None:
    from agentic_swmm.memory.moc_generator import generate_memory_moc

    memory_dir = tmp_path / "memory" / "modeling-memory"
    runs_dir = tmp_path / "runs"
    _seed_memory_index(memory_dir)
    _seed_audit_notes(runs_dir, ["case-a", "case-b", "case-c"])

    out = generate_memory_moc(memory_dir, runs_dir)
    # peak_flow_parse_missing should be linked to case-a and case-b.
    peak_row = next(
        line for line in out.splitlines() if "peak_flow_parse_missing" in line
    )
    assert "case-a" in peak_row
    assert "case-b" in peak_row
    # continuity_parse_missing should be linked to case-b and case-c.
    continuity_row = next(
        line for line in out.splitlines() if "continuity_parse_missing" in line
    )
    assert "case-b" in continuity_row
    assert "case-c" in continuity_row


def test_generate_memory_moc_by_skill_impact_lists_patterns(tmp_path: Path) -> None:
    from agentic_swmm.memory.moc_generator import generate_memory_moc

    memory_dir = tmp_path / "memory" / "modeling-memory"
    runs_dir = tmp_path / "runs"
    _seed_memory_index(memory_dir)
    _seed_audit_notes(runs_dir, ["case-a", "case-b", "case-c"])

    out = generate_memory_moc(memory_dir, runs_dir)
    # "By skill impact" section should mention swmm-runner and at least
    # one pattern that proposed a change against it.
    by_skill_section = out.split("By skill impact", 1)[1]
    assert "swmm-runner" in by_skill_section
    assert "peak_flow_parse_missing" in by_skill_section


def test_generate_memory_moc_writes_to_disk(tmp_path: Path) -> None:
    from agentic_swmm.memory.moc_generator import write_memory_moc

    memory_dir = tmp_path / "memory" / "modeling-memory"
    runs_dir = tmp_path / "runs"
    _seed_memory_index(memory_dir)
    _seed_audit_notes(runs_dir, ["case-a", "case-b", "case-c"])

    path = write_memory_moc(memory_dir, runs_dir)
    assert path == memory_dir / "INDEX.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "type: memory-index" in text
