"""Structural test for the audit-hook -> parametric_memory wiring
(PRD-06 Phase A.5).

After ``trigger_memory_refresh`` runs on an eligible run, a JSONL row
must land in ``memory/modeling-memory/parametric_memory.jsonl``
carrying the run's identifying fields. We assert this end-to-end with
the same fixture pattern other audit-hook tests use.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.memory.audit_hook import trigger_memory_refresh
from agentic_swmm.memory.parametric_memory import recall_parametric


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


class AuditHookParametricMemoryTests(unittest.TestCase):
    def _make_run(self, project_root: Path) -> Path:
        runs_dir = project_root / "runs" / "abc"
        runs_dir.mkdir(parents=True)
        run_dir = runs_dir
        audit_dir = run_dir / "09_audit"
        audit_dir.mkdir()
        (audit_dir / "experiment_provenance.json").write_text(
            json.dumps(_PROVENANCE), encoding="utf-8"
        )
        return run_dir

    def test_parametric_record_appended_for_eligible_run(self) -> None:
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run(project_root)

            # Patch the heavy subprocess + RAG refresh so the test
            # exercises only the in-process wiring. Both targets return
            # ``(0, "")`` so the success path runs to completion.
            with mock.patch(
                "agentic_swmm.memory.audit_hook._summarize_memory_cli",
                return_value=(0, ""),
            ), mock.patch(
                "agentic_swmm.memory.audit_hook._refresh_rag_corpus",
                return_value=(0, ""),
            ):
                result = trigger_memory_refresh(run_dir)

            self.assertFalse(result["skipped"], msg=str(result))
            # The parametric store lives next to lessons_learned.md.
            # ``project_root`` may be under ``/var`` which macOS resolves
            # through ``/private``, so trust the path the hook reports.
            self.assertIn("parametric_memory", result, msg=str(result))
            store = Path(result["parametric_memory"])
            self.assertTrue(
                store.is_file(), f"parametric store missing at {store}"
            )
            rows = recall_parametric(store, {})
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["run_id"], _PROVENANCE["run_id"])
            self.assertEqual(row["case_name"], _PROVENANCE["case_name"])
            self.assertEqual(row["swmm_version"], "5.2.4")
            # Continuity metrics flow through from provenance.
            self.assertAlmostEqual(
                row["qa_metrics"]["runoff_continuity_pct"], -0.18, places=3
            )
            self.assertAlmostEqual(
                row["qa_metrics"]["flow_continuity_pct"], 0.04, places=3
            )

    def test_skipped_run_does_not_write_parametric_record(self) -> None:
        """A skipped run (e.g. ``--no-memory``) must not pollute the store."""
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run(project_root)
            result = trigger_memory_refresh(run_dir, no_memory=True)
        self.assertTrue(result["skipped"])
        # ``--no-memory`` skips before any memory dir is even created.
        self.assertNotIn("parametric_memory", result)


# ---------------------------------------------------------------------------
# Round 5 — audit_hook passes through the 2.0 schema fields when provenance
# carries them, but tolerates their absence.
# ---------------------------------------------------------------------------


class AuditHookSchema2PassThroughTests(unittest.TestCase):
    def _make_run_with_provenance(
        self, project_root: Path, provenance: dict
    ) -> Path:
        runs_dir = project_root / "runs" / "abc"
        runs_dir.mkdir(parents=True)
        run_dir = runs_dir
        audit_dir = run_dir / "09_audit"
        audit_dir.mkdir()
        (audit_dir / "experiment_provenance.json").write_text(
            json.dumps(provenance), encoding="utf-8"
        )
        return run_dir

    def _run_hook(self, run_dir: Path):
        with mock.patch(
            "agentic_swmm.memory.audit_hook._summarize_memory_cli",
            return_value=(0, ""),
        ), mock.patch(
            "agentic_swmm.memory.audit_hook._refresh_rag_corpus",
            return_value=(0, ""),
        ):
            return trigger_memory_refresh(run_dir)

    def test_watershed_classification_block_carried_through(self) -> None:
        prov = dict(_PROVENANCE)
        prov["watershed_classification"] = {
            "size_km2": 12.4,
            "impervious_pct": 38.0,
            "climate": "temperate-marine",
            "land_use_dominant": "suburban-residential",
        }
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run_with_provenance(project_root, prov)
            result = self._run_hook(run_dir)
            store = Path(result["parametric_memory"])
            rows = recall_parametric(store, {})
        self.assertEqual(len(rows), 1)
        ws = rows[0]["watershed_classification"]
        self.assertEqual(ws["size_km2"], 12.4)
        self.assertEqual(ws["land_use_dominant"], "suburban-residential")

    def test_performance_metrics_block_carried_through(self) -> None:
        prov = dict(_PROVENANCE)
        prov["performance_metrics"] = {
            "nse": 0.68,
            "kge": 0.71,
            "pbias_pct": -2.4,
        }
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run_with_provenance(project_root, prov)
            result = self._run_hook(run_dir)
            store = Path(result["parametric_memory"])
            rows = recall_parametric(store, {})
        pm = rows[0]["performance_metrics"]
        self.assertEqual(pm["nse"], 0.68)
        self.assertEqual(pm["kge"], 0.71)

    def test_calibration_status_and_parameter_set_ref_pass_through(self) -> None:
        prov = dict(_PROVENANCE)
        prov["calibration_status"] = "calibrated_against_observed"
        prov["parameter_set_ref"] = "calibration_memory/run_42"
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run_with_provenance(project_root, prov)
            result = self._run_hook(run_dir)
            store = Path(result["parametric_memory"])
            rows = recall_parametric(store, {})
        self.assertEqual(
            rows[0]["calibration_status"], "calibrated_against_observed"
        )
        self.assertEqual(
            rows[0]["parameter_set_ref"], "calibration_memory/run_42"
        )

    def test_invalid_calibration_status_dropped_to_none(self) -> None:
        # An out-of-band value from provenance must NOT crash the
        # write — the audit hook drops it back to ``None`` so the
        # parametric record still lands.
        prov = dict(_PROVENANCE)
        prov["calibration_status"] = "garbage_value"
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run_with_provenance(project_root, prov)
            result = self._run_hook(run_dir)
            self.assertIn("parametric_memory", result, msg=str(result))
            store = Path(result["parametric_memory"])
            rows = recall_parametric(store, {})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["calibration_status"])

    def test_absent_blocks_keep_record_writable_with_safe_defaults(self) -> None:
        # The original provenance fixture has none of the new fields —
        # this is the common-case audit hook input. The record must
        # still land with safe defaults.
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run_with_provenance(project_root, dict(_PROVENANCE))
            result = self._run_hook(run_dir)
            store = Path(result["parametric_memory"])
            rows = recall_parametric(store, {})
        row = rows[0]
        self.assertEqual(row["watershed_classification"], {})
        self.assertEqual(row["performance_metrics"], {})
        self.assertIsNone(row["calibration_status"])
        self.assertIsNone(row["parameter_set_ref"])
        self.assertEqual(row["evidence_runs_count"], 1)
        self.assertEqual(row["schema_version"], "2.0")

    def test_evidence_runs_count_passes_through_when_int(self) -> None:
        prov = dict(_PROVENANCE)
        prov["evidence_runs_count"] = 14
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run_with_provenance(project_root, prov)
            result = self._run_hook(run_dir)
            store = Path(result["parametric_memory"])
            rows = recall_parametric(store, {})
        self.assertEqual(rows[0]["evidence_runs_count"], 14)

    def test_garbage_evidence_runs_count_falls_back_to_1(self) -> None:
        prov = dict(_PROVENANCE)
        prov["evidence_runs_count"] = "not-an-int"
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run_with_provenance(project_root, prov)
            result = self._run_hook(run_dir)
            store = Path(result["parametric_memory"])
            rows = recall_parametric(store, {})
        self.assertEqual(rows[0]["evidence_runs_count"], 1)


if __name__ == "__main__":
    unittest.main()
