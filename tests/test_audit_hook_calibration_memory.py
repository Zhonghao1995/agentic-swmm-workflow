"""Structural test for the audit-hook -> calibration_memory wiring
(PRD-06 Phase B.3).

When the audit pipeline writes an ``experiment_provenance.json`` with
a ``calibration`` block, the memory refresh hook must mirror that block
into ``memory/modeling-memory/calibration_memory.jsonl``. When the
block is absent the hook must silently skip — most runs are not
calibrations.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.memory.audit_hook import trigger_memory_refresh
from agentic_swmm.memory.calibration_memory import recall_calibration


def _provenance_with_calibration() -> dict:
    return {
        "schema_version": "1.1",
        "run_id": "20260519-143022_calib",
        "case_name": "saanich-b8",
        "workflow_mode": "calibration",
        "status": "ok",
        "tools": {
            "python_executable": "/usr/bin/python3",
            "swmm5_version": "5.2.4",
        },
        "metrics": {
            "continuity_error": {
                "name": "continuity_error",
                "values": {"runoff": 0.05, "flow": 0.02},
            },
        },
        "calibration": {
            "algorithm": "sceua",
            "use_case": "stormwater_event",
            "parameters": {"manning_n": 0.013, "imdmax": 0.25},
            "objective_name": "NSE",
            "objective_value": 0.78,
            "secondary_metrics": {"pbias_pct": -3.2, "rmse": 0.043},
            "n_evaluations": 200,
            "wall_time_s": 142.5,
        },
    }


def _provenance_without_calibration() -> dict:
    payload = _provenance_with_calibration()
    payload.pop("calibration", None)
    return payload


class AuditHookCalibrationMemoryTests(unittest.TestCase):
    def _make_run(self, project_root: Path, *, provenance: dict) -> Path:
        runs_dir = project_root / "runs" / "abc"
        runs_dir.mkdir(parents=True)
        audit_dir = runs_dir / "09_audit"
        audit_dir.mkdir()
        (audit_dir / "experiment_provenance.json").write_text(
            json.dumps(provenance), encoding="utf-8"
        )
        return runs_dir

    def _patches(self):
        return (
            mock.patch(
                "agentic_swmm.memory.audit_hook._summarize_memory_cli",
                return_value=(0, ""),
            ),
            mock.patch(
                "agentic_swmm.memory.audit_hook._refresh_rag_corpus",
                return_value=(0, ""),
            ),
        )

    def test_calibration_block_writes_record(self) -> None:
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run(
                project_root, provenance=_provenance_with_calibration()
            )

            cli_patch, rag_patch = self._patches()
            with cli_patch, rag_patch:
                result = trigger_memory_refresh(run_dir)

            self.assertFalse(result["skipped"], msg=str(result))
            self.assertIn("calibration_memory", result, msg=str(result))
            store = Path(result["calibration_memory"])
            self.assertTrue(store.is_file())

            rows = recall_calibration(store, {})
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["run_id"], "20260519-143022_calib")
            self.assertEqual(row["case_name"], "saanich-b8")
            self.assertEqual(row["algorithm"], "sceua")
            self.assertEqual(row["use_case"], "stormwater_event")
            self.assertEqual(row["objective_name"], "NSE")
            self.assertAlmostEqual(row["objective_value"], 0.78, places=6)
            self.assertEqual(row["parameters"]["manning_n"], 0.013)
            self.assertAlmostEqual(
                row["secondary_metrics"]["pbias_pct"], -3.2, places=3
            )
            self.assertEqual(row["swmm5_version"], "5.2.4")
            self.assertEqual(row["n_evaluations"], 200)
            self.assertAlmostEqual(row["wall_time_s"], 142.5, places=3)

    def test_missing_calibration_block_skips_silently(self) -> None:
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run(
                project_root, provenance=_provenance_without_calibration()
            )

            cli_patch, rag_patch = self._patches()
            with cli_patch, rag_patch:
                result = trigger_memory_refresh(run_dir)

        self.assertFalse(result["skipped"], msg=str(result))
        self.assertNotIn("calibration_memory", result)
        # No error logged either — silent skip is the contract.
        self.assertEqual(
            [e for e in result.get("errors", []) if "calibration" in e],
            [],
        )

    def test_calibration_write_error_does_not_block_pipeline(self) -> None:
        """A broken calibration write must not crash the audit pipeline.

        We mock the bridge to raise; the rest of the result dict must
        still be populated and ``skipped`` must remain ``False``.
        """
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = self._make_run(
                project_root, provenance=_provenance_with_calibration()
            )

            cli_patch, rag_patch = self._patches()
            with cli_patch, rag_patch, mock.patch(
                "agentic_swmm.memory.audit_hook._record_calibration_from_provenance",
                side_effect=RuntimeError("boom"),
            ):
                result = trigger_memory_refresh(run_dir)

        self.assertFalse(result["skipped"], msg=str(result))
        self.assertNotIn("calibration_memory", result)
        self.assertTrue(
            any("calibration memory write failed" in e for e in result["errors"]),
            msg=str(result["errors"]),
        )
        # The parametric write still happened — proves the calibration
        # failure was isolated.
        self.assertIn("parametric_memory", result)


if __name__ == "__main__":
    unittest.main()
