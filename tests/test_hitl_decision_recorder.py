"""Tests for ``agentic_swmm.hitl.decision_recorder`` (PRD-Z).

The recorder atomically appends ``human_decisions`` records to a run's
``experiment_provenance.json``. Writes go through a temp file + rename
so a process interrupted mid-write cannot corrupt the JSON the audit
note depends on.

The PRD requires:

* a 3-decision-sequence test (append, append, append; read back; order
  + content preserved).
* atomic-write fault injection (the original file remains readable
  after an interrupted second append).
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.hitl.decision_recorder import (
    HumanDecision,
    append_decision,
    read_decisions,
)


def _seed_provenance(path: Path, schema_version: str = "1.2") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "run_id": "case-a",
                "human_decisions": [],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


class AppendDecisionTests(unittest.TestCase):
    def test_three_decisions_appended_in_order(self) -> None:
        with TemporaryDirectory() as tmp:
            prov = Path(tmp) / "09_audit" / "experiment_provenance.json"
            _seed_provenance(prov)
            decisions = [
                HumanDecision(
                    id="dec-1",
                    action="expert_review_approved",
                    by="alice",
                    at_utc="2026-05-14T08:15:00+00:00",
                    pattern="continuity_error_over_threshold",
                    evidence_ref="06_qa/qa_summary.json",
                    decision_text="Approved by Alice.",
                ),
                HumanDecision(
                    id="dec-2",
                    action="calibration_accept",
                    by="alice",
                    at_utc="2026-05-14T09:01:00+00:00",
                    pattern=None,
                    evidence_ref="09_audit/calibration_summary.json",
                    decision_text=None,
                ),
                HumanDecision(
                    id="dec-3",
                    action="publish",
                    by="bob",
                    at_utc="2026-05-14T10:30:00+00:00",
                    pattern=None,
                    evidence_ref="09_audit/experiment_provenance.json",
                    decision_text="Approved for publication.",
                ),
            ]
            for dec in decisions:
                append_decision(prov, dec)
            read_back = read_decisions(prov)
        self.assertEqual([d.id for d in read_back], ["dec-1", "dec-2", "dec-3"])
        self.assertEqual(read_back[1].action, "calibration_accept")
        self.assertEqual(read_back[2].by, "bob")
        self.assertIsNone(read_back[1].pattern)

    def test_append_creates_human_decisions_field_when_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            prov = Path(tmp) / "09_audit" / "experiment_provenance.json"
            prov.parent.mkdir(parents=True, exist_ok=True)
            # v1.1 provenance — no human_decisions field at all.
            prov.write_text(
                json.dumps({"schema_version": "1.1", "run_id": "case-a"}),
                encoding="utf-8",
            )
            decision = HumanDecision(
                id="dec-1",
                action="expert_review_denied",
                by="alice",
                at_utc="2026-05-14T08:15:00+00:00",
                pattern="continuity_error_over_threshold",
                evidence_ref="06_qa/qa_summary.json",
                decision_text=None,
            )
            append_decision(prov, decision)
            data = json.loads(prov.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], "1.2")
        self.assertEqual(len(data["human_decisions"]), 1)
        self.assertEqual(data["human_decisions"][0]["action"], "expert_review_denied")

    def test_interrupted_rename_leaves_original_intact(self) -> None:
        """Atomic write contract: a failed rename must not corrupt the
        existing provenance file."""
        with TemporaryDirectory() as tmp:
            prov = Path(tmp) / "09_audit" / "experiment_provenance.json"
            _seed_provenance(prov)
            # Seed one good decision first.
            append_decision(
                prov,
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
            original_content = prov.read_text(encoding="utf-8")

            # Force the next rename to fail mid-flight.
            def _boom(*args: object, **kwargs: object) -> None:
                raise OSError("simulated rename failure")

            with mock.patch("os.replace", side_effect=_boom):
                with self.assertRaises(OSError):
                    append_decision(
                        prov,
                        HumanDecision(
                            id="dec-2",
                            action="expert_review_denied",
                            by="alice",
                            at_utc="2026-05-14T09:00:00+00:00",
                            pattern="peak_flow_deviation_over_threshold",
                            evidence_ref="06_qa/qa_summary.json",
                            decision_text=None,
                        ),
                    )

            # Original file is untouched + no stray tmp file lingering.
            self.assertEqual(prov.read_text(encoding="utf-8"), original_content)
            stray = [
                p for p in prov.parent.iterdir() if p.name.endswith(".tmp")
            ]
            self.assertEqual(stray, [], f"unexpected tmp leftover: {stray}")

    def test_read_decisions_returns_empty_list_when_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            prov = Path(tmp) / "09_audit" / "experiment_provenance.json"
            _seed_provenance(prov)
            # Reset to v1.1 (no field at all) for this case.
            prov.write_text(
                json.dumps({"schema_version": "1.1", "run_id": "case-a"}),
                encoding="utf-8",
            )
            self.assertEqual(read_decisions(prov), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
