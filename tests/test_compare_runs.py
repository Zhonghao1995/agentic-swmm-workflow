"""Tests for ``agentic_swmm.agent.swmm_runtime.compare`` (PRD-06 Phase B.1).

``compare_runs`` takes two SWMM run directories, pulls continuity metrics
through :func:`postflight_qa`, and returns a typed :class:`RunComparison`
with a verdict, per-metric diffs, and plain-language notes.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime.compare import (
    DEFAULT_METRICS,
    MetricDiff,
    RunComparison,
    compare_runs,
    render_comparison_table,
)


_HEALTHY_RPT = """\
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)
  ------------------------------------------------------------

  **************************        Volume         Depth
  Runoff Quantity Continuity     hectare-m            mm
  **************************     ---------       -------
  Total Precipitation ......         0.092        26.208
  Surface Runoff ...........         0.037        10.536
  Continuity Error (%) .....        -0.171


  **************************        Volume        Volume
  Flow Routing Continuity        hectare-m      10^6 ltr
  **************************     ---------     ---------
  External Outflow .........         0.037         0.369
  Continuity Error (%) .....         0.500
"""


_BETTER_HEALTHY_RPT = _HEALTHY_RPT.replace(
    "Continuity Error (%) .....        -0.171",
    "Continuity Error (%) .....        -0.050",
).replace(
    "Continuity Error (%) .....         0.500",
    "Continuity Error (%) .....         0.100",
)


_BAD_CONTINUITY_RPT = _HEALTHY_RPT.replace(
    "Continuity Error (%) .....        -0.171",
    "Continuity Error (%) .....        12.500",  # FAIL (>10%)
).replace(
    "Continuity Error (%) .....         0.500",
    "Continuity Error (%) .....         3.200",  # WARN (>1% <5%)
)


def _write_run_dir(parent: Path, name: str, rpt_body: str | None = None) -> Path:
    run_dir = parent / name
    run_dir.mkdir()
    if rpt_body is not None:
        (run_dir / "model.rpt").write_text(rpt_body, encoding="utf-8")
    return run_dir


def _write_provenance(run_dir: Path, run_id: str) -> None:
    audit = run_dir / "09_audit"
    audit.mkdir(exist_ok=True)
    (audit / "experiment_provenance.json").write_text(
        json.dumps({"run_id": run_id}), encoding="utf-8"
    )


class IdentityResolutionTests(unittest.TestCase):
    def test_run_id_read_from_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "dir-a", _HEALTHY_RPT)
            b = _write_run_dir(base, "dir-b", _HEALTHY_RPT)
            _write_provenance(a, "run-a-alpha")
            _write_provenance(b, "run-b-beta")
            result = compare_runs(a, b)
        self.assertEqual(result.run_a_id, "run-a-alpha")
        self.assertEqual(result.run_b_id, "run-b-beta")

    def test_run_id_falls_back_to_dir_name(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "fallback-a", _HEALTHY_RPT)
            b = _write_run_dir(base, "fallback-b", _HEALTHY_RPT)
            result = compare_runs(a, b)
        self.assertEqual(result.run_a_id, "fallback-a")
        self.assertEqual(result.run_b_id, "fallback-b")


class IncomparableTests(unittest.TestCase):
    def test_both_runs_missing_rpt_is_incomparable(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "empty-a")
            b = _write_run_dir(base, "empty-b")
            result = compare_runs(a, b)
        self.assertEqual(result.verdict, "incomparable")
        self.assertTrue(any("neither run" in note for note in result.notes))

    def test_only_run_a_missing_rpt_is_incomparable(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "no-rpt-a")
            b = _write_run_dir(base, "has-rpt-b", _HEALTHY_RPT)
            result = compare_runs(a, b)
        self.assertEqual(result.verdict, "incomparable")
        self.assertTrue(any("run A" in note for note in result.notes))

    def test_only_run_b_missing_rpt_is_incomparable(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "has-rpt-a", _HEALTHY_RPT)
            b = _write_run_dir(base, "no-rpt-b")
            result = compare_runs(a, b)
        self.assertEqual(result.verdict, "incomparable")
        self.assertTrue(any("run B" in note for note in result.notes))


class VerdictTests(unittest.TestCase):
    def test_both_pass_with_lower_continuity_a_is_a_better(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _BETTER_HEALTHY_RPT)
            b = _write_run_dir(base, "b", _HEALTHY_RPT)
            result = compare_runs(a, b)
        self.assertEqual(result.verdict, "a_better")
        self.assertEqual(
            result.metric_diffs["runoff_continuity_pct"].classification_a, "PASS"
        )
        self.assertEqual(
            result.metric_diffs["runoff_continuity_pct"].classification_b, "PASS"
        )

    def test_a_fail_b_pass_yields_b_better(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _BAD_CONTINUITY_RPT)
            b = _write_run_dir(base, "b", _HEALTHY_RPT)
            result = compare_runs(a, b)
        self.assertEqual(result.verdict, "b_better")
        self.assertEqual(
            result.metric_diffs["runoff_continuity_pct"].classification_a, "FAIL"
        )
        # Notes should mention the FAIL/PASS asymmetry for talking-point clarity.
        self.assertTrue(any("fails runoff_continuity_pct" in n for n in result.notes))

    def test_identical_metrics_are_tie(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _HEALTHY_RPT)
            b = _write_run_dir(base, "b", _HEALTHY_RPT)
            result = compare_runs(a, b)
        self.assertEqual(result.verdict, "tie")


class MetricDiffTests(unittest.TestCase):
    def test_default_metric_set_present(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _HEALTHY_RPT)
            b = _write_run_dir(base, "b", _BETTER_HEALTHY_RPT)
            result = compare_runs(a, b)
        for name in DEFAULT_METRICS:
            self.assertIn(name, result.metric_diffs)

    def test_metric_filter_restricts_diff_set(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _HEALTHY_RPT)
            b = _write_run_dir(base, "b", _BETTER_HEALTHY_RPT)
            result = compare_runs(a, b, metrics=["runoff_continuity_pct"])
        self.assertIn("runoff_continuity_pct", result.metric_diffs)
        self.assertNotIn("flow_continuity_pct", result.metric_diffs)

    def test_delta_abs_signed_and_pct_skipped_when_a_is_zero(self) -> None:
        # Build a fixture where run A has runoff_continuity exactly 0.
        zero_rpt = _HEALTHY_RPT.replace(
            "Continuity Error (%) .....        -0.171",
            "Continuity Error (%) .....         0.000",
        )
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", zero_rpt)
            b = _write_run_dir(base, "b", _HEALTHY_RPT)
            result = compare_runs(a, b)
        diff = result.metric_diffs["runoff_continuity_pct"]
        self.assertIsNotNone(diff.delta_abs)
        # delta_pct must be None — undefined when value_a is 0.
        self.assertIsNone(diff.delta_pct)


class JSONSerializationTests(unittest.TestCase):
    def test_to_dict_round_trips_through_json(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _HEALTHY_RPT)
            b = _write_run_dir(base, "b", _BETTER_HEALTHY_RPT)
            result = compare_runs(a, b)
        payload = result.to_dict()
        # Must be JSON-encodable.
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["verdict"], result.verdict)
        self.assertEqual(decoded["run_a_id"], result.run_a_id)
        self.assertIn("metric_diffs", decoded)
        self.assertIn("runoff_continuity_pct", decoded["metric_diffs"])


class RenderTableTests(unittest.TestCase):
    def test_render_includes_run_ids_and_verdict(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _HEALTHY_RPT)
            b = _write_run_dir(base, "b", _HEALTHY_RPT)
            result = compare_runs(a, b)
            text = render_comparison_table(result)
        self.assertIn("run A: a", text)
        self.assertIn("run B: b", text)
        self.assertIn("verdict:", text)
        self.assertIn("runoff_continuity_pct", text)


class DataclassShapeTests(unittest.TestCase):
    def test_metric_diff_to_dict_has_all_fields(self) -> None:
        diff = MetricDiff(
            metric="runoff_continuity_pct",
            value_a=0.1,
            value_b=0.2,
            delta_abs=0.1,
            delta_pct=100.0,
            classification_a="PASS",
            classification_b="PASS",
        )
        out = diff.to_dict()
        for key in (
            "metric",
            "value_a",
            "value_b",
            "delta_abs",
            "delta_pct",
            "classification_a",
            "classification_b",
        ):
            self.assertIn(key, out)

    def test_run_comparison_default_verdict_is_tie(self) -> None:
        rc = RunComparison(run_a_id="a", run_b_id="b")
        self.assertEqual(rc.verdict, "tie")
        self.assertEqual(rc.metric_diffs, {})
        self.assertEqual(rc.notes, [])


if __name__ == "__main__":
    unittest.main()
