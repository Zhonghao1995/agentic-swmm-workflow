"""Tests for ``agentic_swmm.memory.user_baseline`` (Round 6 / PRD-07 Phase 4).

PR #152 shipped the advisory threshold proposer. Round 6 lifts it into
a binding helper that classifies new runs against the user's own
historical distribution when there are enough samples. The library
thresholds remain the fallback.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.user_baseline import (
    UserBaseline,
    compute_user_baseline,
)


def _write_store(
    store_path: Path,
    *,
    rows: list[dict[str, object]],
) -> None:
    """Write JSONL rows to a parametric_memory store."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with store_path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def _row(
    *,
    run_id: str,
    case_name: str,
    use_case: str,
    runoff_continuity: float,
    flow_continuity: float = 0.5,
    recorded_utc: str = "2026-04-01T00:00:00Z",
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
        "recorded_utc": recorded_utc,
    }


class MinObservationsTests(unittest.TestCase):
    """Below 5 matching rows → returns None."""

    def test_four_rows_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write_store(
                store,
                rows=[
                    _row(
                        run_id=f"r{i}",
                        case_name="todcreek",
                        use_case="stormwater_event",
                        runoff_continuity=0.2,
                    )
                    for i in range(4)
                ],
            )
            result = compute_user_baseline(
                store,
                case_name="todcreek",
                use_case="stormwater_event",
                metric_path="qa_metrics.runoff_continuity_pct",
            )
        self.assertIsNone(result)

    def test_min_observations_override(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write_store(
                store,
                rows=[
                    _row(
                        run_id=f"r{i}",
                        case_name="todcreek",
                        use_case="stormwater_event",
                        runoff_continuity=0.2,
                    )
                    for i in range(3)
                ],
            )
            result = compute_user_baseline(
                store,
                case_name="todcreek",
                use_case="stormwater_event",
                metric_path="qa_metrics.runoff_continuity_pct",
                min_observations=3,
            )
        self.assertIsNotNone(result)


class PercentileComputationTests(unittest.TestCase):
    """≥ 5 matching rows → returns a UserBaseline with sensible percentiles."""

    def test_ten_rows_returns_baseline(self) -> None:
        values = [0.1, 0.15, 0.18, 0.2, 0.22, 0.25, 0.3, 0.5, 0.8, 1.2]
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write_store(
                store,
                rows=[
                    _row(
                        run_id=f"r{i}",
                        case_name="todcreek",
                        use_case="stormwater_event",
                        runoff_continuity=v,
                    )
                    for i, v in enumerate(values)
                ],
            )
            result = compute_user_baseline(
                store,
                case_name="todcreek",
                use_case="stormwater_event",
                metric_path="qa_metrics.runoff_continuity_pct",
            )
        assert isinstance(result, UserBaseline)
        self.assertEqual(result.n_observations, 10)
        self.assertEqual(result.case_name, "todcreek")
        self.assertEqual(result.use_case, "stormwater_event")
        self.assertEqual(
            result.metric_path, "qa_metrics.runoff_continuity_pct"
        )
        # p50 in this sample is around 0.235; p95 close to the tail.
        self.assertGreater(result.p50, 0.0)
        self.assertGreater(result.p95, result.p50)
        self.assertGreaterEqual(result.p99, result.p95)
        # Mean / std are populated.
        self.assertGreater(result.mean, 0.0)
        self.assertGreaterEqual(result.std, 0.0)
        # Sources includes all run ids.
        self.assertEqual(len(result.sources), 10)


class FilteringTests(unittest.TestCase):
    """Case and use_case filters partition rows independently."""

    def test_case_name_filter(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write_store(
                store,
                rows=[
                    *[
                        _row(
                            run_id=f"a{i}",
                            case_name="todcreek",
                            use_case="stormwater_event",
                            runoff_continuity=0.2,
                        )
                        for i in range(6)
                    ],
                    *[
                        _row(
                            run_id=f"b{i}",
                            case_name="tecnopolo",
                            use_case="stormwater_event",
                            runoff_continuity=10.0,
                        )
                        for i in range(6)
                    ],
                ],
            )
            result = compute_user_baseline(
                store,
                case_name="todcreek",
                use_case="stormwater_event",
                metric_path="qa_metrics.runoff_continuity_pct",
            )
        assert result is not None
        self.assertEqual(result.n_observations, 6)
        # All values came from the 0.2 batch.
        self.assertAlmostEqual(result.mean, 0.2, places=3)

    def test_use_case_filter(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write_store(
                store,
                rows=[
                    *[
                        _row(
                            run_id=f"a{i}",
                            case_name="todcreek",
                            use_case="stormwater_event",
                            runoff_continuity=0.2,
                        )
                        for i in range(6)
                    ],
                    *[
                        _row(
                            run_id=f"b{i}",
                            case_name="todcreek",
                            use_case="calibration",
                            runoff_continuity=15.0,
                        )
                        for i in range(6)
                    ],
                ],
            )
            result = compute_user_baseline(
                store,
                case_name="todcreek",
                use_case="stormwater_event",
                metric_path="qa_metrics.runoff_continuity_pct",
            )
        assert result is not None
        self.assertEqual(result.n_observations, 6)
        self.assertAlmostEqual(result.mean, 0.2, places=3)


class DottedPathExtractionTests(unittest.TestCase):
    """Metric path extraction tolerates nested + missing keys."""

    def test_nested_metric_path(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write_store(
                store,
                rows=[
                    _row(
                        run_id=f"r{i}",
                        case_name="todcreek",
                        use_case="stormwater_event",
                        runoff_continuity=0.1,
                        flow_continuity=0.4 + 0.01 * i,
                    )
                    for i in range(6)
                ],
            )
            flow = compute_user_baseline(
                store,
                case_name="todcreek",
                use_case="stormwater_event",
                metric_path="qa_metrics.flow_continuity_pct",
            )
        assert flow is not None
        self.assertGreater(flow.mean, 0.4)


class MalformedRowsTolerated(unittest.TestCase):
    """Malformed rows / missing fields do not poison the baseline."""

    def test_torn_rows_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            valid_rows = [
                _row(
                    run_id=f"r{i}",
                    case_name="todcreek",
                    use_case="stormwater_event",
                    runoff_continuity=0.2,
                )
                for i in range(6)
            ]
            _write_store(store, rows=valid_rows)
            # Append a torn line at the end.
            with store.open("a", encoding="utf-8") as fh:
                fh.write('{"schema_version": "2.0", "ru')
            result = compute_user_baseline(
                store,
                case_name="todcreek",
                use_case="stormwater_event",
                metric_path="qa_metrics.runoff_continuity_pct",
            )
        assert result is not None
        self.assertEqual(result.n_observations, 6)

    def test_missing_metric_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            # 5 rows with metric, 1 without
            rows = [
                _row(
                    run_id=f"r{i}",
                    case_name="todcreek",
                    use_case="stormwater_event",
                    runoff_continuity=0.2,
                )
                for i in range(5)
            ]
            missing_metric = {
                "schema_version": "2.0",
                "run_id": "r-missing",
                "case_name": "todcreek",
                "model_structure": {"use_case": "stormwater_event"},
                "qa_metrics": {},
                "recorded_utc": "2026-04-01T00:00:00Z",
            }
            rows.append(missing_metric)
            _write_store(store, rows=rows)
            result = compute_user_baseline(
                store,
                case_name="todcreek",
                use_case="stormwater_event",
                metric_path="qa_metrics.runoff_continuity_pct",
            )
        assert result is not None
        self.assertEqual(result.n_observations, 5)


class LookbackTests(unittest.TestCase):
    """lookback_days filters out old rows."""

    def test_lookback_filters_old_rows(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        recent_ts = (
            now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        old_ts = (
            (now - timedelta(days=365))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write_store(
                store,
                rows=[
                    *[
                        _row(
                            run_id=f"new{i}",
                            case_name="todcreek",
                            use_case="stormwater_event",
                            runoff_continuity=0.2,
                            recorded_utc=recent_ts,
                        )
                        for i in range(6)
                    ],
                    *[
                        _row(
                            run_id=f"old{i}",
                            case_name="todcreek",
                            use_case="stormwater_event",
                            runoff_continuity=10.0,
                            recorded_utc=old_ts,
                        )
                        for i in range(6)
                    ],
                ],
            )
            result = compute_user_baseline(
                store,
                case_name="todcreek",
                use_case="stormwater_event",
                metric_path="qa_metrics.runoff_continuity_pct",
                lookback_days=30,
            )
        assert result is not None
        self.assertEqual(result.n_observations, 6)
        self.assertAlmostEqual(result.mean, 0.2, places=3)


class MissingStoreTests(unittest.TestCase):
    """No store → None, no crash."""

    def test_missing_store_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "nonexistent.jsonl"
            result = compute_user_baseline(
                store,
                case_name="todcreek",
                use_case="stormwater_event",
                metric_path="qa_metrics.runoff_continuity_pct",
            )
        self.assertIsNone(result)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
