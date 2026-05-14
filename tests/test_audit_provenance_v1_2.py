"""Tests for ``agentic_swmm.audit.provenance_v1_2`` (PRD-Z schema 1.2).

The 1.2 schema bumps ``schema_version`` from ``"1.1"`` to ``"1.2"`` and
adds an optional ``human_decisions`` array. v1.1 readers must still
work (the field is optional), and the audit-note renderer must surface
a new ``## Human Decisions`` table whenever the array is non-empty.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.audit.provenance_v1_2 import (
    SCHEMA_VERSION,
    migrate_from_v1_1,
    read,
    render_human_decisions_section,
    write,
)
from agentic_swmm.hitl.decision_recorder import HumanDecision, append_decision


class SchemaVersionTests(unittest.TestCase):
    def test_schema_version_constant(self) -> None:
        self.assertEqual(SCHEMA_VERSION, "1.2")


class ReadWriteTests(unittest.TestCase):
    def test_round_trip_writes_v1_2(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiment_provenance.json"
            write(path, {"run_id": "case-a", "human_decisions": []})
            data = read(path)
        self.assertEqual(data["schema_version"], "1.2")
        self.assertEqual(data["run_id"], "case-a")
        self.assertEqual(data["human_decisions"], [])

    def test_read_v1_1_treats_missing_human_decisions_as_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiment_provenance.json"
            path.write_text(
                json.dumps({"schema_version": "1.1", "run_id": "case-a"}),
                encoding="utf-8",
            )
            data = read(path)
        # In-memory upgrade: schema_version becomes 1.2, decisions defaulted.
        self.assertEqual(data["schema_version"], "1.2")
        self.assertEqual(data["human_decisions"], [])
        self.assertEqual(data["run_id"], "case-a")

    def test_migrate_from_v1_1_writes_v1_2_in_place(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiment_provenance.json"
            path.write_text(
                json.dumps({"schema_version": "1.1", "run_id": "case-a"}),
                encoding="utf-8",
            )
            migrate_from_v1_1(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], "1.2")
        self.assertEqual(raw["human_decisions"], [])

    def test_v1_1_provenance_still_readable_after_decision_append(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiment_provenance.json"
            path.write_text(
                json.dumps({"schema_version": "1.1", "run_id": "case-a"}),
                encoding="utf-8",
            )
            append_decision(
                path,
                HumanDecision(
                    id="dec-1",
                    action="expert_review_approved",
                    by="alice",
                    at_utc="2026-05-14T08:15:00+00:00",
                    pattern="continuity_error_over_threshold",
                    evidence_ref="06_qa/qa_summary.json",
                    decision_text=None,
                ),
            )
            data = read(path)
        self.assertEqual(data["schema_version"], "1.2")
        self.assertEqual(len(data["human_decisions"]), 1)


class RenderHumanDecisionsSectionTests(unittest.TestCase):
    def test_empty_decisions_renders_empty_string(self) -> None:
        self.assertEqual(render_human_decisions_section([]), "")

    def test_non_empty_renders_section_with_table(self) -> None:
        decisions = [
            {
                "id": "dec-1",
                "action": "expert_review_approved",
                "by": "alice",
                "at_utc": "2026-05-14T08:15:00+00:00",
                "pattern": "continuity_error_over_threshold",
                "evidence_ref": "06_qa/qa_summary.json",
                "decision_text": "Continuity error looked like solver instability, but Alice confirmed routing parameters had been overridden.",
            },
            {
                "id": "dec-2",
                "action": "calibration_accept",
                "by": "alice",
                "at_utc": "2026-05-14T09:01:00+00:00",
                "pattern": None,
                "evidence_ref": "09_audit/calibration_summary.json",
                "decision_text": None,
            },
        ]
        out = render_human_decisions_section(decisions)
        self.assertIn("## Human Decisions", out)
        self.assertIn("expert_review_approved", out)
        self.assertIn("calibration_accept", out)
        self.assertIn("alice", out)
        # Pipe-table header row present.
        self.assertIn("| Action | By |", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
