"""Audit-pipeline integration of the HITL threshold evaluator (PRD-Z).

After ``aiswmm audit`` writes ``09_audit/experiment_provenance.json``
the audit command must also call
:func:`agentic_swmm.hitl.threshold_evaluator.evaluate` against the QA
summary and, if any hits are returned, write the hits list to
``09_audit/threshold_hits.json``. This is the "partial" integration the
PRD calls out — the data is captured even though full
``request_expert_review`` triggering remains a follow-up.

These tests exercise the audit command's ``main`` directly so we do not
shell out (the existing audit_run.py subprocess is mocked).
"""

from __future__ import annotations

import argparse
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.commands import audit as audit_cmd
from agentic_swmm.utils.subprocess_runner import CommandResult


def _seed_run(tmp: Path, qa_summary: dict) -> Path:
    run_dir = tmp / "runs" / "case-a"
    audit_dir = run_dir / "09_audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "experiment_provenance.json").write_text(
        json.dumps({"schema_version": "1.1", "run_id": "case-a"}),
        encoding="utf-8",
    )
    qa = run_dir / "06_qa"
    qa.mkdir()
    (qa / "qa_summary.json").write_text(json.dumps(qa_summary), encoding="utf-8")
    return run_dir


def _run_audit_main(run_dir: Path) -> int:
    args = argparse.Namespace(
        run_dir=run_dir,
        compare_to=None,
        case_name=None,
        workflow_mode=None,
        objective=None,
        obsidian=False,
        no_memory=True,
        no_rag=True,
        rebuild=False,
    )
    # Mock the audit subprocess so the existing CommandResult path runs
    # without actually re-executing audit_run.py end-to-end.
    fake_result = CommandResult(
        command=["python3", "audit_run.py"],
        return_code=0,
        started_at_utc="2026-05-14T08:15:00+00:00",
        finished_at_utc="2026-05-14T08:15:01+00:00",
        stdout=json.dumps({"ok": True, "run_id": "case-a"}),
        stderr="",
    )
    with mock.patch(
        "agentic_swmm.commands.audit.run_command", return_value=fake_result
    ), mock.patch(
        # Block the memory hook so the test stays hermetic.
        "agentic_swmm.memory.audit_hook.trigger_memory_refresh",
        return_value={"skipped": True, "reason": "test"},
    ), mock.patch(
        "agentic_swmm.commands.audit._write_moc", return_value=None
    ):
        return audit_cmd.main(args)


class AuditThresholdHitsTests(unittest.TestCase):
    def test_writes_threshold_hits_when_continuity_high(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run(
                Path(tmp),
                qa_summary={"continuity": {"flow_routing": 6.5}},
            )
            rc = _run_audit_main(run_dir)
            hits_path = run_dir / "09_audit" / "threshold_hits.json"
            self.assertEqual(rc, 0)
            self.assertTrue(hits_path.is_file(), f"expected {hits_path}")
            data = json.loads(hits_path.read_text(encoding="utf-8"))
        patterns = [h["pattern"] for h in data["hits"]]
        self.assertIn("continuity_error_over_threshold", patterns)

    def test_no_file_written_when_no_hits(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run(
                Path(tmp),
                qa_summary={"continuity": {"flow_routing": 0.1}},
            )
            rc = _run_audit_main(run_dir)
            hits_path = run_dir / "09_audit" / "threshold_hits.json"
            self.assertEqual(rc, 0)
            self.assertFalse(hits_path.exists(), f"unexpected: {hits_path}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
