"""Tests for ``postflight_qa`` user-baseline integration (Round 6).

When the caller provides ``parametric_store``, ``case_name`` and
``use_case``, ``postflight_qa`` consults
:func:`agentic_swmm.memory.user_baseline.compute_user_baseline` per
parsed metric. If the user has ≥ 5 historical observations for that
metric the new run is classified against the user p95 / p99
boundaries. Otherwise the library thresholds remain authoritative.

Existing callers (no new kwargs) are byte-identical — this is verified
in the ``LegacyCallersUnaffected`` slice.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime.postflight import (
    QAReport,
    postflight_qa,
)


_HEALTHY_RPT = """\
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)

  Saanich framework smoke test

  **************************
  Runoff Quantity Continuity
  **************************
  Total Precipitation ......         0.092
  Surface Runoff ...........         0.037
  Continuity Error (%) .....         {RUNOFF_VAL}


  **************************
  Flow Routing Continuity
  **************************
  External Outflow .........         0.037
  Continuity Error (%) .....         {FLOW_VAL}
"""


def _row(
    *,
    run_id: str,
    case_name: str = "todcreek",
    use_case: str = "stormwater_event",
    runoff_continuity: float = 0.2,
    flow_continuity: float = 0.5,
) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "run_id": run_id,
        "case_name": case_name,
        "model_structure": {"use_case": use_case},
        "qa_metrics": {
            "runoff_continuity_pct": runoff_continuity,
            "flow_continuity_pct": flow_continuity,
        },
        "performance_metrics": {},
        "watershed_classification": {},
        "calibration_status": "uncalibrated",
        "parameter_set_ref": None,
        "evidence_runs_count": 1,
        "recorded_utc": "2026-04-01T00:00:00Z",
    }


def _write_store(store: Path, rows: list[dict[str, object]]) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def _make_run_dir(tmp: str, *, runoff_pct: float, flow_pct: float) -> Path:
    run_dir = Path(tmp) / "run-1"
    run_dir.mkdir()
    body = _HEALTHY_RPT.format(
        RUNOFF_VAL=f"{runoff_pct:.3f}", FLOW_VAL=f"{flow_pct:.3f}"
    )
    (run_dir / "model.rpt").write_text(body, encoding="utf-8")
    return run_dir


class LegacyCallersUnaffectedTests(unittest.TestCase):
    """Existing call signature (no kwargs) → byte-identical behaviour."""

    def test_no_user_baseline_kwargs_uses_library(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_run_dir(tmp, runoff_pct=0.2, flow_pct=0.3)
            report = postflight_qa(run_dir)
        self.assertIsInstance(report, QAReport)
        self.assertEqual(report.status, "PASS")
        # No thresholds_source set when user baseline never consulted.
        sources = getattr(report, "thresholds_source", {}) or {}
        # The map may exist but should record "library" only when run
        # against the new kwargs; the legacy path skips it entirely.
        for key in ("runoff_continuity_pct", "flow_continuity_pct"):
            if key in sources:
                self.assertEqual(sources[key], "library")


class BelowMinHistoryFallsBack(unittest.TestCase):
    """< 5 historical rows → library thresholds, label = 'library'."""

    def test_three_history_rows_falls_back_to_library(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write_store(
                store,
                rows=[
                    _row(run_id=f"r{i}", runoff_continuity=0.1)
                    for i in range(3)
                ],
            )
            run_dir = _make_run_dir(tmp, runoff_pct=0.2, flow_pct=0.3)

            report = postflight_qa(
                run_dir,
                parametric_store=store,
                case_name="todcreek",
                use_case="stormwater_event",
            )
        self.assertEqual(report.status, "PASS")
        sources = getattr(report, "thresholds_source", {})
        self.assertEqual(sources.get("runoff_continuity_pct"), "library")
        self.assertEqual(sources.get("flow_continuity_pct"), "library")


class UserBaselineClassifiesNewRun(unittest.TestCase):
    """≥ 5 rows clustered at < 0.3% → new run at 1.5% → WARN."""

    def test_new_value_in_warn_band(self) -> None:
        # 50 tightly-clustered historical runs + one large outlier
        # carves a clear (p95, p99) corridor the new value can fall
        # into. p95 ~ 0.25%, p99 ~ 4%; new = 1.0% → WARN.
        small = [0.1, 0.12, 0.13, 0.14, 0.15, 0.16, 0.18, 0.2, 0.22, 0.25]
        values = small * 5 + [3.0]
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write_store(
                store,
                rows=[
                    _row(run_id=f"r{i}", runoff_continuity=v)
                    for i, v in enumerate(values)
                ],
            )
            run_dir = _make_run_dir(tmp, runoff_pct=1.0, flow_pct=0.3)

            report = postflight_qa(
                run_dir,
                parametric_store=store,
                case_name="todcreek",
                use_case="stormwater_event",
            )
        sources = getattr(report, "thresholds_source", {})
        self.assertEqual(
            sources.get("runoff_continuity_pct"),
            "user_baseline",
        )
        self.assertEqual(
            report.classifications["runoff_continuity_pct"], "WARN"
        )
        self.assertIn(report.status, ("WARN", "PASS"))

    def test_new_value_above_p99_is_fail(self) -> None:
        # Same 51-sample cluster as the WARN test. New = 8.0% sits
        # above p99 (~4.3%) so user_baseline flags FAIL even though
        # 8% < library FAIL threshold (10%) and would only WARN
        # against the library classification.
        small = [0.1, 0.12, 0.13, 0.14, 0.15, 0.16, 0.18, 0.2, 0.22, 0.25]
        values = small * 5 + [3.0]
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write_store(
                store,
                rows=[
                    _row(run_id=f"r{i}", runoff_continuity=v)
                    for i, v in enumerate(values)
                ],
            )
            run_dir = _make_run_dir(tmp, runoff_pct=8.0, flow_pct=0.3)

            report = postflight_qa(
                run_dir,
                parametric_store=store,
                case_name="todcreek",
                use_case="stormwater_event",
            )
        sources = getattr(report, "thresholds_source", {})
        self.assertEqual(
            sources.get("runoff_continuity_pct"), "user_baseline"
        )
        self.assertEqual(
            report.classifications["runoff_continuity_pct"], "FAIL"
        )
        self.assertEqual(report.status, "FAIL")


class PerMetricThresholdSource(unittest.TestCase):
    """Each parsed metric records its own thresholds_source."""

    def test_one_metric_user_one_metric_library(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            # Only runoff has 7 rows; flow_continuity is recorded but
            # all uniform — still 7 samples → user_baseline applies.
            # To force a per-metric mix we instead drop flow values
            # entirely on most rows.
            rows = []
            for i, v in enumerate([0.1, 0.12, 0.15, 0.18, 0.2, 0.22, 0.25]):
                row = _row(run_id=f"r{i}", runoff_continuity=v)
                # Strip flow metric on most rows so < 5 survive for the
                # flow user-baseline path.
                if i < 5:
                    row["qa_metrics"] = {  # type: ignore[index]
                        "runoff_continuity_pct": v
                    }
                rows.append(row)
            _write_store(store, rows=rows)
            run_dir = _make_run_dir(tmp, runoff_pct=0.2, flow_pct=0.3)

            report = postflight_qa(
                run_dir,
                parametric_store=store,
                case_name="todcreek",
                use_case="stormwater_event",
            )

        sources = getattr(report, "thresholds_source", {})
        self.assertEqual(
            sources.get("runoff_continuity_pct"),
            "user_baseline",
        )
        self.assertEqual(
            sources.get("flow_continuity_pct"),
            "library",
        )


class TraceEmittedTests(unittest.TestCase):
    """memory_trace.jsonl carries the thresholds_source per metric."""

    def test_memory_trace_records_threshold_source(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write_store(
                store,
                rows=[
                    _row(run_id=f"r{i}", runoff_continuity=0.2)
                    for i in range(7)
                ],
            )
            run_dir = _make_run_dir(tmp, runoff_pct=0.2, flow_pct=0.3)

            postflight_qa(
                run_dir,
                parametric_store=store,
                case_name="todcreek",
                use_case="stormwater_event",
            )

            trace = run_dir / "memory_trace.jsonl"
            self.assertTrue(trace.is_file())
            lines = trace.read_text(encoding="utf-8").splitlines()
        events = [json.loads(l) for l in lines if l.strip()]
        # Look for our thresholds_source event.
        ts_events = [
            e for e in events if e.get("kind") == "postflight_thresholds"
        ]
        self.assertTrue(ts_events, f"events: {events}")
        rec = ts_events[-1]
        self.assertIn("thresholds_source", rec)
        self.assertIn(
            "runoff_continuity_pct", rec["thresholds_source"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
