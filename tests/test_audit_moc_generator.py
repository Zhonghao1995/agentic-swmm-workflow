"""Tests for agentic_swmm.audit.moc_generator.

PRD section "Module: MOC generator".
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


SWMM_NOTE_PASS = """---
type: experiment-audit
status: pass
case: case-a
date: 2026-05-13
has_plot: true
has_qa: true
failed_qa_checks: 0
---

# Experiment Audit - case-a
"""

SWMM_NOTE_FAIL = """---
type: experiment-audit
status: fail
case: case-b
date: 2026-05-12
has_plot: false
has_qa: true
failed_qa_checks: 2
---

# Experiment Audit - case-b
"""

CHAT_NOTE = """---
type: chat-session
case: inspect-project
date: 2026-05-13
goal: "inspect"
status: ok
tags:
  - agentic-swmm
  - chat-session
---

# Chat Session - inspect-project
"""


def _seed_swmm(run_dir: Path, note_text: str) -> None:
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "experiment_note.md").write_text(note_text, encoding="utf-8")
    (audit / "experiment_provenance.json").write_text(
        json.dumps({"schema_version": "1.1", "status": "pass"}), encoding="utf-8"
    )


def _seed_chat(session_dir: Path) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "session_state.json").write_text("{}", encoding="utf-8")
    (session_dir / "agent_trace.jsonl").write_text("", encoding="utf-8")
    (session_dir / "chat_note.md").write_text(CHAT_NOTE, encoding="utf-8")


class GenerateMocTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _fixture_runs(self) -> dict[str, Path]:
        a = self.root / "case-a"
        b = self.root / "benchmarks" / "case-b"
        deep = self.root / "external-case-candidates" / "zen" / "month" / "runner"
        chat = self.root / "2026-05-13" / "100000_demo_chat"
        unaudited = self.root / "raw-case-candidates" / "case-x"
        _seed_swmm(a, SWMM_NOTE_PASS)
        _seed_swmm(b, SWMM_NOTE_FAIL)
        _seed_swmm(deep, SWMM_NOTE_PASS.replace("case-a", "case-deep"))
        _seed_chat(chat)
        (unaudited / "05_builder").mkdir(parents=True)
        (unaudited / "manifest.json").write_text("{}", encoding="utf-8")
        return {"a": a, "b": b, "deep": deep, "chat": chat, "unaudited": unaudited}

    def test_returns_string_markdown(self) -> None:
        from agentic_swmm.audit.moc_generator import generate_moc

        self._fixture_runs()
        md = generate_moc(self.root)
        self.assertIsInstance(md, str)
        self.assertTrue(md.startswith("---\n"))

    def test_dataview_frontmatter_present(self) -> None:
        from agentic_swmm.audit.moc_generator import generate_moc

        self._fixture_runs()
        md = generate_moc(self.root)
        head = md.split("---", 2)[1]
        self.assertIn("type: runs-index", head)

    def test_two_tables_by_date_and_by_bucket(self) -> None:
        from agentic_swmm.audit.moc_generator import generate_moc

        self._fixture_runs()
        md = generate_moc(self.root)
        self.assertIn("## By date", md)
        self.assertIn("## By bucket", md)
        # SWMM and chat rows interleave in the date table.
        date_section = md.split("## By date", 1)[1].split("##", 1)[0]
        self.assertIn("experiment-audit", date_section)
        self.assertIn("chat-session", date_section)

    def test_emits_wikilink_for_each_audited_run(self) -> None:
        from agentic_swmm.audit.moc_generator import generate_moc

        self._fixture_runs()
        md = generate_moc(self.root)
        # Each audited run shows up as a wikilink to its note. We accept
        # either the [[path]] or [[path|alias]] form.
        for needle in (
            "case-a/09_audit/experiment_note",
            "benchmarks/case-b/09_audit/experiment_note",
            "external-case-candidates/zen/month/runner/09_audit/experiment_note",
            "2026-05-13/100000_demo_chat/chat_note",
        ):
            self.assertIn(needle, md, f"missing wikilink target: {needle}")

    def test_unaudited_runs_section_lists_unaudited(self) -> None:
        from agentic_swmm.audit.moc_generator import generate_moc

        fixtures = self._fixture_runs()
        md = generate_moc(self.root)
        self.assertIn("## Unaudited run dirs", md)
        unaudited_section = md.split("## Unaudited run dirs", 1)[1]
        rel = fixtures["unaudited"].relative_to(self.root).as_posix()
        self.assertIn(rel, unaudited_section)
        # The PRD requires a "run `aiswmm audit <path>`" hint.
        self.assertIn("aiswmm audit", unaudited_section)

    def test_archive_subtree_is_excluded(self) -> None:
        from agentic_swmm.audit.moc_generator import generate_moc

        self._fixture_runs()
        archived = self.root / ".archive" / "agent-1778717638"
        archived.mkdir(parents=True)
        (archived / "agent_trace.jsonl").write_text("", encoding="utf-8")
        md = generate_moc(self.root)
        self.assertNotIn(".archive", md)
        self.assertNotIn("agent-1778717638", md)


if __name__ == "__main__":
    unittest.main()
