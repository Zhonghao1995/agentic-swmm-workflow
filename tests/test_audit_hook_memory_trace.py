"""Audit-hook -> memory_trace wiring (PRD-07 Phase 2 slice 10).

The parametric_memory write that the audit hook performs is the
first call site that gets a transparency line. It proves the
``log_memory_decision`` plumbing works end-to-end before the next
agent wires it into the disambiguator / QA replacement.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.memory_trace import (
    MEMORY_TRACE_FILENAME,
    read_memory_trace,
)
from agentic_swmm.memory.audit_hook import trigger_memory_refresh


_PROVENANCE = {
    "schema_version": "1.1",
    "run_id": "20260519-143022_urbancase",
    "case_name": "saanich-b8",
    "workflow_mode": "prepared_inp_cli",
    "status": "ok",
    "tools": {
        "python_executable": "/usr/bin/python3",
        "swmm5_version": "5.2.4",
    },
    "metrics": {
        "continuity_error": {
            "name": "continuity_error",
            "values": {"runoff": -0.18, "flow": 0.04},
        },
    },
}


class AuditHookMemoryTraceTests(unittest.TestCase):
    def _make_run(self, project_root: Path) -> Path:
        runs_dir = project_root / "runs" / "abc"
        runs_dir.mkdir(parents=True)
        audit_dir = runs_dir / "09_audit"
        audit_dir.mkdir()
        (audit_dir / "experiment_provenance.json").write_text(
            json.dumps(_PROVENANCE), encoding="utf-8"
        )
        return runs_dir

    def test_eligible_run_writes_one_memory_trace_line(self) -> None:
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run(project_root)

            with mock.patch(
                "agentic_swmm.memory.audit_hook._summarize_memory_cli",
                return_value=(0, ""),
            ), mock.patch(
                "agentic_swmm.memory.audit_hook._refresh_rag_corpus",
                return_value=(0, ""),
            ):
                result = trigger_memory_refresh(run_dir)

            self.assertFalse(result["skipped"], msg=str(result))
            self.assertIn("memory_trace", result, msg=str(result))

            trace_path = Path(result["memory_trace"])
            self.assertTrue(trace_path.is_file())
            self.assertEqual(trace_path.name, MEMORY_TRACE_FILENAME)

            entries = read_memory_trace(run_dir)

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(
            entry["decision_point"], "audit_hook_parametric_write"
        )
        self.assertEqual(entry["confidence"], "auto_complete")
        self.assertEqual(entry["decision_taken"], "recorded")
        # Phase A bridges qa_metrics from provenance into parametric_memory;
        # the trace should record that the runoff metric was visible.
        self.assertIn("saanich-b8", entry["memory_context_summary"])

    def test_skipped_run_writes_no_memory_trace(self) -> None:
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run(project_root)
            result = trigger_memory_refresh(run_dir, no_memory=True)

            self.assertTrue(result["skipped"])
            self.assertNotIn("memory_trace", result)
            self.assertEqual(read_memory_trace(run_dir), [])

    def test_failed_parametric_write_does_not_write_trace(self) -> None:
        """If the parametric write returns None, the trace line is skipped."""
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run(project_root)

            with mock.patch(
                "agentic_swmm.memory.audit_hook._summarize_memory_cli",
                return_value=(0, ""),
            ), mock.patch(
                "agentic_swmm.memory.audit_hook._refresh_rag_corpus",
                return_value=(0, ""),
            ), mock.patch(
                "agentic_swmm.memory.audit_hook._record_parametric_from_provenance",
                return_value=None,
            ):
                result = trigger_memory_refresh(run_dir)

            self.assertFalse(result["skipped"], msg=str(result))
            self.assertNotIn("memory_trace", result)
            self.assertEqual(read_memory_trace(run_dir), [])


if __name__ == "__main__":
    unittest.main()
