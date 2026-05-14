"""Tests for ``agentic_swmm.gap_fill.recorder`` (PRD-GF-CORE).

The recorder owns two side-effects per gap decision:

1. **gap-decisions ledger** — appends a fully-serialised
   :class:`GapDecision` to ``<run_dir>/09_audit/gap_decisions.json``.
   The on-disk shape is ``{"schema_version": "1", "decisions": [...]}``
   so a fresh ledger and a partially-filled one share a parse path.
2. **human-decisions cross-link** — for every gap decision, an entry
   is appended to ``experiment_provenance.json.human_decisions`` via
   :func:`agentic_swmm.hitl.decision_recorder.append_decision`. The
   ``action`` field is ``"gap_fill_L1"`` or ``"gap_fill_L3"`` so a
   reviewer can grep the provenance ledger for gap-fill events.

The atomic-write contract mirrors the existing
``decision_recorder``: tmp-file + ``os.replace`` so a process killed
mid-write leaves the previous valid JSON intact.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.gap_fill.protocol import (
    GapDecision,
    ProposerInfo,
    new_decision_id,
    new_gap_id,
)
from agentic_swmm.gap_fill.recorder import (
    record_gap_decisions,
    read_gap_decisions,
)
from agentic_swmm.hitl.decision_recorder import read_decisions


def _make_l3_decision() -> GapDecision:
    return GapDecision(
        decision_id=new_decision_id(),
        gap_id=new_gap_id(),
        severity="L3",
        field="manning_n_imperv",
        proposer=ProposerInfo(
            source="registry",
            confidence="HIGH",
            registry_ref="defaults_table.yaml#manning_n_paved",
            literature_ref="EPA SWMM 5 Reference Manual, Table 8-1",
        ),
        proposed_value=0.013,
        final_value=0.013,
        proposer_overridden=False,
        decided_by="human",
        decided_at="2026-05-14T10:00:00Z",
        resume_mode="tool_retry",
        human_decisions_ref=None,
    )


def _make_l1_decision() -> GapDecision:
    return GapDecision(
        decision_id=new_decision_id(),
        gap_id=new_gap_id(),
        severity="L1",
        field="rainfall_file",
        proposer=ProposerInfo(source="human", confidence="HIGH"),
        proposed_value=None,
        final_value="/cases/case-a/observed/rain.csv",
        proposer_overridden=False,
        decided_by="human",
        decided_at="2026-05-14T10:01:00Z",
        resume_mode="tool_retry",
        human_decisions_ref=None,
    )


class RecordGapDecisionsTests(unittest.TestCase):
    def test_records_l3_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            dec = _make_l3_decision()
            record_gap_decisions(run_dir, [dec])

            ledger_path = run_dir / "09_audit" / "gap_decisions.json"
            self.assertTrue(ledger_path.is_file())
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "1")
            self.assertEqual(len(payload["decisions"]), 1)
            self.assertEqual(payload["decisions"][0]["field"], "manning_n_imperv")
            self.assertEqual(payload["decisions"][0]["final_value"], 0.013)

    def test_cross_links_human_decisions(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            dec = _make_l3_decision()
            record_gap_decisions(run_dir, [dec])

            prov_path = run_dir / "09_audit" / "experiment_provenance.json"
            decisions = read_decisions(prov_path)
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0].action, "gap_fill_L3")
            # The recorder populates the cross-link reference back into
            # the gap decision so a reader of gap_decisions.json can
            # find the matching provenance entry.
            payload = json.loads(
                (run_dir / "09_audit" / "gap_decisions.json").read_text(encoding="utf-8")
            )
            ref = payload["decisions"][0]["human_decisions_ref"]
            self.assertIsNotNone(ref)
            self.assertIn("human_decisions", ref)

    def test_l1_uses_gap_fill_l1_action(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            dec = _make_l1_decision()
            record_gap_decisions(run_dir, [dec])
            prov_path = run_dir / "09_audit" / "experiment_provenance.json"
            decisions = read_decisions(prov_path)
            self.assertEqual(decisions[0].action, "gap_fill_L1")

    def test_appends_across_multiple_calls(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            record_gap_decisions(run_dir, [_make_l1_decision()])
            record_gap_decisions(run_dir, [_make_l3_decision()])
            ledger = read_gap_decisions(run_dir)
            self.assertEqual(len(ledger), 2)
            self.assertEqual(ledger[0].severity, "L1")
            self.assertEqual(ledger[1].severity, "L3")

            prov_path = run_dir / "09_audit" / "experiment_provenance.json"
            decisions = read_decisions(prov_path)
            self.assertEqual(len(decisions), 2)
            actions = {d.action for d in decisions}
            self.assertEqual(actions, {"gap_fill_L1", "gap_fill_L3"})

    def test_records_batch_of_two_decisions(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            record_gap_decisions(run_dir, [_make_l1_decision(), _make_l3_decision()])
            ledger = read_gap_decisions(run_dir)
            self.assertEqual(len(ledger), 2)
            prov_path = run_dir / "09_audit" / "experiment_provenance.json"
            self.assertEqual(len(read_decisions(prov_path)), 2)

    def test_read_gap_decisions_empty_when_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(read_gap_decisions(Path(tmp)), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
