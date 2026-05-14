"""Unit tests for ``agentic_swmm.memory.audit_to_memory`` (PRD M5)."""

from __future__ import annotations

import json
from pathlib import Path


_SWMM_NOTE = """---
type: experiment-note
case: case-a
schema_version: 1.1
status: pass
tags:
  - agentic-swmm
---

# Experiment note for case-a

failure_patterns:
- peak_flow_parse_missing
"""


_CHAT_NOTE = """---
type: chat-session
case: chat-2026-05-13
date: 2026-05-13
schema_version: 1.1
status: ok
tags:
  - agentic-swmm
  - chat-session
  - planner-stuck
---

# Chat session 2026-05-13

User asked about something.
"""


def test_extract_memory_entry_from_experiment_note(tmp_path: Path) -> None:
    from agentic_swmm.memory.audit_to_memory import extract_memory_entry

    note_path = tmp_path / "runs" / "case-a" / "09_audit" / "experiment_note.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text(_SWMM_NOTE, encoding="utf-8")
    provenance_path = note_path.parent / "experiment_provenance.json"
    provenance_path.write_text(
        json.dumps(
            {
                "run_id": "case-a",
                "case_name": "Case A",
                "schema_version": "1.1",
                "failure_patterns": ["peak_flow_parse_missing"],
            }
        ),
        encoding="utf-8",
    )

    entry = extract_memory_entry(note_path)
    assert entry["source_type"] == "run_record"
    assert entry["case_name"] in {"case-a", "Case A"}
    assert entry["schema_version"] == "1.1"
    assert "peak_flow_parse_missing" in entry["failure_patterns"]


def test_extract_memory_entry_from_chat_note(tmp_path: Path) -> None:
    from agentic_swmm.memory.audit_to_memory import extract_memory_entry

    note_path = tmp_path / "runs" / "2026-05-13" / "session-1" / "chat_note.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text(_CHAT_NOTE, encoding="utf-8")

    entry = extract_memory_entry(note_path)
    assert entry["source_type"] == "chat"
    assert entry["case_name"] == "chat-2026-05-13"
    assert entry["schema_version"] == "1.1"
    # Chat-derived failure_patterns: tag 'planner-stuck' -> pattern 'planner_stuck'.
    assert "planner_stuck" in entry["failure_patterns"]


def test_extract_memory_entry_chat_with_no_failure_tags(tmp_path: Path) -> None:
    from agentic_swmm.memory.audit_to_memory import extract_memory_entry

    note_path = tmp_path / "runs" / "2026-05-13" / "session-2" / "chat_note.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text(
        "---\ntype: chat-session\ncase: chat-empty\nschema_version: 1.1\nstatus: ok\ntags:\n  - agentic-swmm\n---\n",
        encoding="utf-8",
    )

    entry = extract_memory_entry(note_path)
    assert entry["source_type"] == "chat"
    assert entry["failure_patterns"] == []


def test_summariser_refuses_mixed_schema_versions(tmp_path: Path) -> None:
    """A schema bump must refuse to silently mix runs.

    We exercise the lightweight refusal helper directly so we do not
    need to drive the 965-LOC summarise_memory.py end-to-end.
    """
    from agentic_swmm.memory.audit_to_memory import assert_uniform_schema

    entries = [
        {"schema_version": "1.0", "source_path": "a"},
        {"schema_version": "1.1", "source_path": "b"},
    ]
    import pytest

    with pytest.raises(RuntimeError, match=r"schema"):
        assert_uniform_schema(entries)


def test_summariser_accepts_uniform_schema_versions(tmp_path: Path) -> None:
    from agentic_swmm.memory.audit_to_memory import assert_uniform_schema

    entries = [
        {"schema_version": "1.1", "source_path": "a"},
        {"schema_version": "1.1", "source_path": "b"},
    ]
    # Must not raise.
    assert_uniform_schema(entries) == "1.1"
