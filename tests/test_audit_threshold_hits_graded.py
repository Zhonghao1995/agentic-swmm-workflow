"""Audit wiring for the graded gate against REAL QA shapes.

The crisp integration test feeds the evaluator's dotted shape; these
tests feed the shape the QA stage actually writes (a ``checks`` list),
so the projection seam is exercised end to end: real shape in, graded
hit out, thresholds-doc hash and severity counts on the audit payload
(spec: fuzzy-hitl-gates).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.commands import audit as audit_cmd
from agentic_swmm.utils.subprocess_runner import CommandResult

FIXTURE = Path(__file__).parent / "fixtures" / "hitl" / "qa_summary_real_shape.json"


def _real_shape_qa(flow_routing: float) -> dict:
    qa = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for check in qa["checks"]:
        if check.get("id") == "continuity_parsed":
            check["detail"]["flow_routing"] = flow_routing
    return qa


def _seed_run(tmp: Path, qa_summary: dict) -> Path:
    # 07_qa is the canonical QA stage; the crisp test covers legacy 06_qa.
    run_dir = tmp / "runs" / "case-graded"
    audit_dir = run_dir / "09_audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "experiment_provenance.json").write_text(
        json.dumps({"schema_version": "1.1", "run_id": "case-graded"}),
        encoding="utf-8",
    )
    qa = run_dir / "07_qa"
    qa.mkdir()
    (qa / "qa_summary.json").write_text(json.dumps(qa_summary), encoding="utf-8")
    return run_dir


def _run_audit_main(run_dir: Path) -> tuple[int, str]:
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
    fake_result = CommandResult(
        command=["python3", "audit_run.py"],
        return_code=0,
        started_at_utc="2026-08-30T08:15:00+00:00",
        finished_at_utc="2026-08-30T08:15:01+00:00",
        stdout=json.dumps({"ok": True, "run_id": "case-graded"}),
        stderr="",
    )
    out = io.StringIO()
    with mock.patch(
        "agentic_swmm.commands.audit.run_command", return_value=fake_result
    ), mock.patch(
        "agentic_swmm.memory.audit_hook.trigger_memory_refresh",
        return_value={"skipped": True, "reason": "test"},
    ), mock.patch(
        "agentic_swmm.commands.audit._write_moc", return_value=None
    ), contextlib.redirect_stdout(out):
        rc = audit_cmd.main(args)
    return rc, out.getvalue()


class GradedAuditIntegrationTests(unittest.TestCase):
    def test_real_shape_medium_band_writes_warn_hit(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run(Path(tmp), _real_shape_qa(-6.0))
            rc, stdout = _run_audit_main(run_dir)
            hits_path = run_dir / "09_audit" / "threshold_hits.json"
            self.assertEqual(rc, 0)
            self.assertTrue(hits_path.is_file(), f"expected {hits_path}")
            data = json.loads(hits_path.read_text(encoding="utf-8"))
        hit = next(
            h
            for h in data["hits"]
            if h["pattern"] == "continuity_error_over_threshold"
        )
        # -6.0 is compared on its absolute value: medium band, warn.
        self.assertEqual(hit["severity"], "warn")
        self.assertEqual(hit["level"], "medium")
        self.assertAlmostEqual(hit["measured_value"], 6.0)
        self.assertAlmostEqual(hit["memberships"]["medium"], 0.8)
        self.assertEqual(hit["bands"], {"fine": 1.0, "centre": 5.0, "bad": 10.0})
        sha = data.get("thresholds_doc_sha256", "")
        self.assertRegex(sha, r"^[0-9a-f]{64}$")
        payload = json.loads(stdout)
        self.assertEqual(payload["threshold_hit_levels"], {"block": 0, "warn": 1})

    def test_real_shape_low_band_writes_nothing(self) -> None:
        # The pristine fixture value (-0.004) sits deep in the low band.
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run(Path(tmp), _real_shape_qa(-0.004))
            rc, _ = _run_audit_main(run_dir)
            hits_path = run_dir / "09_audit" / "threshold_hits.json"
            self.assertEqual(rc, 0)
            self.assertFalse(hits_path.exists(), f"unexpected: {hits_path}")

    def test_sensitivity_indices_feed_the_gate(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run(Path(tmp), _real_shape_qa(-0.004))
            (run_dir / "09_audit" / "sensitivity_indices.json").write_text(
                json.dumps(
                    {
                        "method": "sobol",
                        "indices": {
                            "imperv": {"S_i": 0.85},
                            "width": {"S_i": 0.1},
                        },
                    }
                ),
                encoding="utf-8",
            )
            rc, _ = _run_audit_main(run_dir)
            hits_path = run_dir / "09_audit" / "threshold_hits.json"
            self.assertEqual(rc, 0)
            self.assertTrue(hits_path.is_file(), f"expected {hits_path}")
            data = json.loads(hits_path.read_text(encoding="utf-8"))
        patterns = [h["pattern"] for h in data["hits"]]
        self.assertIn("sobol_first_order_dominant", patterns)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
