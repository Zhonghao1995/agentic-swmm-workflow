"""Regression tests: failure-pattern classification must not call healthy runs failures.

Bug (found 2026-08-08 failure-record mining, Rank 3): the tracked memory
store's top "Repeated Failure Patterns" were mostly noise from complete,
qa-passing runs. Three mechanisms in ``summarize_memory.py``:

1. **Provenance-only trust**: legacy flat-layout runs (and runs audited
   by an older audit version) carry ``exists: false`` artifact records
   even when the physical file sits in the run dir. The classifier
   never cross-checked the filesystem, so ``missing_rpt`` /
   ``missing_out`` / ``missing_inp`` fired against complete runs.
2. **Falsy-zero metrics**: ``if not peak`` / ``if not continuity``
   treated a legitimately parsed ``0.0`` (dry-run peak, perfect
   continuity) as a parse gap.
3. **Over-escalation**: metric-parse gaps escalated to ``partial_run``,
   so a run with qa=pass and all runner artifacts on disk was recorded
   as a partial run because one metric went unextracted.

Fix under test: physical-artifact patterns require the file to be
absent from the run dir too; metric checks use ``is None``; only
missing-artifact patterns escalate to ``partial_run``. Genuine failures
must still classify exactly as before (the existing pin in
``test_swmm_modeling_memory.py`` covers the missing-rpt-on-disk case).
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills/swmm-modeling-memory/scripts/summarize_memory.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "_summarize_memory_classification_test", _SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MOD = _load_module()


_AUDIT_FILES_ALL = [
    "experiment_provenance.json",
    "comparison.json",
    "experiment_note.md",
    "model_diagnostics.json",
]


def _healthy_provenance(**metric_overrides) -> dict:
    metrics = {
        "swmm_return_code": 0,
        "peak_flow": {"value": 1.2, "node": "O1"},
        "continuity_error": -0.1,
    }
    metrics.update(metric_overrides)
    return {
        "run_id": "case-x",
        "status": "pass",
        "qa": {"status": "pass", "fail_count": 0, "pass_count": 2},
        "metrics": metrics,
        "artifacts": {},
    }


def _detect(run_dir: Path, provenance: dict, artifacts_missing: list[str]):
    return _MOD.detect_failure_patterns(
        run_dir=run_dir,
        provenance=provenance,
        comparison={},
        model_diagnostics={},
        artifacts_missing=artifacts_missing,
        audit_files_found=_AUDIT_FILES_ALL,
    )


class FilesystemCrossCheckTests(unittest.TestCase):
    """Stale ``exists: false`` provenance records lose to files on disk."""

    def test_flat_layout_run_with_artifacts_on_disk_is_not_partial(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (run_dir / "model.inp").write_text("[TITLE]\n", encoding="utf-8")
            (run_dir / "model.rpt").write_text("rpt", encoding="utf-8")
            (run_dir / "model.out").write_bytes(b"\x00")
            patterns = _detect(
                run_dir,
                _healthy_provenance(),
                # A stale audit recorded all three as missing.
                ["model_inp", "runner_rpt", "runner_out"],
            )
        self.assertEqual(patterns, ["no_detected_failure"])

    def test_inp_in_subdir_counts_as_present(self) -> None:
        """Canonical layouts keep the INP under 05_builder/ (or trials/)."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            builder = run_dir / "05_builder"
            builder.mkdir()
            (builder / "model.inp").write_text("[TITLE]\n", encoding="utf-8")
            patterns = _detect(run_dir, _healthy_provenance(), ["model_inp"])
        self.assertNotIn("missing_inp", patterns)

    def test_genuinely_absent_artifacts_still_classify(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            patterns = _detect(
                run_dir,
                _healthy_provenance(),
                ["model_inp", "runner_rpt", "runner_out"],
            )
        self.assertIn("missing_inp", patterns)
        self.assertIn("missing_rpt", patterns)
        self.assertIn("missing_out", patterns)
        self.assertIn("partial_run", patterns)

    def test_no_provenance_and_no_inp_still_flags_missing_inp(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            patterns = _detect(run_dir, {}, [])
        self.assertIn("missing_inp", patterns)


class MetricZeroTests(unittest.TestCase):
    """A parsed 0.0 is a value, not a parse gap."""

    def test_zero_metrics_are_not_parse_gaps(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (run_dir / "model.inp").write_text("[TITLE]\n", encoding="utf-8")
            patterns = _detect(
                run_dir,
                _healthy_provenance(peak_flow=0.0, continuity_error=0.0),
                [],
            )
        self.assertNotIn("peak_flow_parse_missing", patterns)
        self.assertNotIn("continuity_parse_missing", patterns)
        self.assertEqual(patterns, ["no_detected_failure"])

    def test_none_metrics_still_flag_parse_gaps(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (run_dir / "model.inp").write_text("[TITLE]\n", encoding="utf-8")
            patterns = _detect(
                run_dir,
                _healthy_provenance(peak_flow=None, continuity_error=None),
                [],
            )
        self.assertIn("peak_flow_parse_missing", patterns)
        self.assertIn("continuity_parse_missing", patterns)


class EscalationScopeTests(unittest.TestCase):
    """Only missing ARTIFACTS make a run partial; parse gaps do not."""

    def test_parse_gap_alone_does_not_escalate_to_partial_run(self) -> None:
        """The mined real-world case (real-todcreek-minimal): qa pass,
        inp/rpt/out on disk, peak parsed and validated, continuity not
        extracted by the audit. Recorded as partial_run before the fix."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (run_dir / "model.inp").write_text("[TITLE]\n", encoding="utf-8")
            (run_dir / "model.rpt").write_text("rpt", encoding="utf-8")
            (run_dir / "model.out").write_bytes(b"\x00")
            patterns = _detect(
                run_dir,
                _healthy_provenance(continuity_error=None),
                [],
            )
        self.assertEqual(patterns, ["continuity_parse_missing"])

    def test_missing_artifact_still_escalates(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (run_dir / "model.inp").write_text("[TITLE]\n", encoding="utf-8")
            patterns = _detect(run_dir, _healthy_provenance(), ["runner_rpt"])
        self.assertIn("missing_rpt", patterns)
        self.assertIn("partial_run", patterns)


class BuildRecordEndToEndTests(unittest.TestCase):
    """Whole-record path over a synthetic flat-layout run dir."""

    def test_flat_layout_healthy_run_summarizes_clean(self) -> None:
        import json

        with TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            run_dir = runs_dir / "flat-case"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (run_dir / "model.inp").write_text("[TITLE]\n", encoding="utf-8")
            (run_dir / "model.rpt").write_text("rpt", encoding="utf-8")
            (run_dir / "model.out").write_bytes(b"\x00")
            prov = _healthy_provenance()
            # Stale audit: claims rpt/out missing although they exist.
            prov["artifacts"] = {
                "model_inp": {"exists": True, "relative_path": "runs/flat-case/model.inp"},
                "runner_rpt": {"exists": False, "relative_path": None},
                "runner_out": {"exists": False, "relative_path": None},
            }
            (run_dir / "experiment_provenance.json").write_text(
                json.dumps(prov), encoding="utf-8"
            )
            (run_dir / "experiment_note.md").write_text(
                "# Note\n\n## Evidence Boundary\n\n- Synthetic test.\n",
                encoding="utf-8",
            )
            (run_dir / "comparison.json").write_text(
                json.dumps({"comparison_available": False, "checks": [], "warnings": []}),
                encoding="utf-8",
            )
            (run_dir / "model_diagnostics.json").write_text(
                json.dumps({"status": "pass", "diagnostics": []}), encoding="utf-8"
            )
            record = _MOD.build_record(run_dir, runs_dir)

        self.assertEqual(record["failure_patterns"], ["no_detected_failure"])
        self.assertEqual(record["qa_status"], "pass")
        # The stale provenance claims stay visible as missing evidence —
        # only the FAILURE classification stops trusting them blindly.
        self.assertIn("runner_rpt", record["artifacts_missing"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
