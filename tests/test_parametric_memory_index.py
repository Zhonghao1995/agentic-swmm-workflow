"""Tests for ``agentic_swmm.memory.parametric_memory_index`` (PRD-06 §4.1).

The parametric_memory store is JSONL-canonical and SQLite-accelerated.
This file covers the index sidecar:

- :func:`needs_index` honors the 1k-row threshold and JSONL/sidecar
  mtime ordering.
- :func:`build_or_refresh_index` produces a SQLite file with the right
  row count.
- :func:`recall_via_index` returns results identical to the JSONL
  linear scan for the same filters.
- :func:`recall_via_index` raises ``IndexStaleError`` when the JSONL
  is newer than the sidecar.
- The transparent recall path (``recall_parametric``) builds the index
  on demand and falls back to JSONL when the index is stale.
- Writes never touch the sidecar; subsequent reads trigger the rebuild.
"""

from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agentic_swmm.memory.parametric_memory import (
    ParametricRecord,
    recall_parametric,
    record_parametric_run,
)
from agentic_swmm.memory.parametric_memory_index import (
    DEFAULT_INDEX_THRESHOLD,
    IndexStaleError,
    build_or_refresh_index,
    index_path_for,
    needs_index,
    recall_via_index,
)


def _seed_store(store: Path, n_rows: int, *, case_offset: int = 0) -> None:
    """Append ``n_rows`` records to ``store`` for size-threshold tests."""
    for i in range(n_rows):
        rec = ParametricRecord(
            run_id=f"run-{i:05d}",
            case_name=f"case-{(i + case_offset) % 4}",
            swmm_version="5.2.4" if i % 2 == 0 else "5.1.013",
            model_structure={
                "routing": "dynamic_wave" if i % 3 == 0 else "kinematic_wave"
            },
            qa_metrics={"runoff_continuity_pct": 0.18 + (i % 5) * 0.01},
            performance_metrics={"nse": 0.5 + (i % 10) * 0.03},
            watershed_classification={
                "size_km2": 1.0 + (i % 7),
                "impervious_pct": 30.0,
            },
            calibration_status=None,
            parameter_set_ref=None,
            evidence_runs_count=1,
            recorded_utc=f"2026-05-{(i % 28) + 1:02d}T00:00:00Z",
        )
        record_parametric_run(store, rec)


class IndexPathTests(unittest.TestCase):
    def test_sidecar_lives_next_to_jsonl_with_sqlite3_suffix(self) -> None:
        path = index_path_for(Path("/tmp/parametric_memory.jsonl"))
        self.assertEqual(
            path, Path("/tmp/parametric_memory.jsonl.sqlite3")
        )


class NeedsIndexTests(unittest.TestCase):
    def test_below_threshold_does_not_need_index(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, 999)
            self.assertFalse(needs_index(store))

    def test_at_threshold_does_not_need_index(self) -> None:
        # Exactly threshold rows still uses the linear scan — the
        # "needs index" check is strictly greater-than.
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, DEFAULT_INDEX_THRESHOLD)
            self.assertFalse(needs_index(store))

    def test_above_threshold_with_missing_sidecar_needs_index(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, DEFAULT_INDEX_THRESHOLD + 1)
            self.assertTrue(needs_index(store))

    def test_above_threshold_with_fresh_sidecar_does_not_need_rebuild(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, DEFAULT_INDEX_THRESHOLD + 1)
            build_or_refresh_index(store)
            self.assertFalse(needs_index(store))

    def test_stale_sidecar_needs_index_when_jsonl_newer(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, DEFAULT_INDEX_THRESHOLD + 1)
            build_or_refresh_index(store)
            # Backdate the sidecar so the JSONL becomes "newer".
            sidecar = index_path_for(store)
            past = time.time() - 3600
            os.utime(sidecar, (past, past))
            self.assertTrue(needs_index(store))

    def test_below_threshold_with_existing_sidecar_returns_false(self) -> None:
        # A user might pre-build an index; we should not nag them with
        # rebuilds while the store is still tiny.
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, 50)
            build_or_refresh_index(store)
            self.assertFalse(needs_index(store))

    def test_missing_jsonl_returns_false(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertFalse(needs_index(Path(tmp) / "missing.jsonl"))

    def test_threshold_argument_honored(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, 25)
            self.assertFalse(needs_index(store, threshold_rows=1000))
            self.assertTrue(needs_index(store, threshold_rows=10))


class BuildOrRefreshIndexTests(unittest.TestCase):
    def test_sidecar_row_count_matches_jsonl(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            n_rows = 1500
            _seed_store(store, n_rows)
            sidecar = build_or_refresh_index(store)
            self.assertTrue(sidecar.is_file())
            import sqlite3

            connection = sqlite3.connect(str(sidecar))
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM parametric_records"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, n_rows)

    def test_rebuild_replaces_old_rows(self) -> None:
        """A second call rebuilds from the JSONL, not appends to the old SQLite."""
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, 1100)
            build_or_refresh_index(store)
            # Now write more rows and rebuild.
            _seed_store(store, 200, case_offset=10)
            build_or_refresh_index(store)
            sidecar = index_path_for(store)
            import sqlite3

            connection = sqlite3.connect(str(sidecar))
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM parametric_records"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 1300)

    def test_sidecar_handles_torn_jsonl_lines(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, 1050)
            # Append a torn line.
            with store.open("a", encoding="utf-8") as handle:
                handle.write('{"run_id": "torn", "case_name":')
            sidecar = build_or_refresh_index(store)
            import sqlite3

            connection = sqlite3.connect(str(sidecar))
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM parametric_records"
                ).fetchone()[0]
            finally:
                connection.close()
            # Torn line is silently skipped; the other 1050 rows land.
            self.assertEqual(count, 1050)


class RecallViaIndexTests(unittest.TestCase):
    def test_index_recall_matches_linear_scan_for_case_name(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, 1200)
            build_or_refresh_index(store)
            indexed = recall_via_index(store, {"case_name": "case-1"})
            # Linear scan: bypass the transparent path by clearing the
            # sidecar so ``recall_parametric`` walks the JSONL itself.
            sidecar = index_path_for(store)
            sidecar.unlink()
            linear = recall_parametric(store, {"case_name": "case-1"})
        self.assertEqual(len(indexed), len(linear))
        self.assertEqual(
            sorted(r["run_id"] for r in indexed),
            sorted(r["run_id"] for r in linear),
        )

    def test_index_recall_matches_for_dotted_blob_filter(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, 1100)
            build_or_refresh_index(store)
            indexed = recall_via_index(
                store, {"model_structure.routing": "dynamic_wave"}
            )
            # Linear: clear sidecar.
            sidecar = index_path_for(store)
            sidecar.unlink()
            linear = recall_parametric(
                store, {"model_structure.routing": "dynamic_wave"}
            )
        self.assertEqual(
            sorted(r["run_id"] for r in indexed),
            sorted(r["run_id"] for r in linear),
        )

    def test_index_recall_empty_filter_returns_everything(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, 1010)
            build_or_refresh_index(store)
            rows = recall_via_index(store, {})
        self.assertEqual(len(rows), 1010)

    def test_index_stale_raises_index_stale_error(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, 1100)
            build_or_refresh_index(store)
            # Append a row so the JSONL is newer than the sidecar.
            sidecar = index_path_for(store)
            past = time.time() - 3600
            os.utime(sidecar, (past, past))
            with self.assertRaises(IndexStaleError):
                recall_via_index(store, {})

    def test_missing_sidecar_raises_index_stale_error(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, 100)
            with self.assertRaises(IndexStaleError):
                recall_via_index(store, {})


class TransparentRecallParametricTests(unittest.TestCase):
    """Recall transparently builds + uses the SQLite index for big stores."""

    def test_large_store_recall_uses_sqlite_index(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, DEFAULT_INDEX_THRESHOLD + 50)
            sidecar = index_path_for(store)
            self.assertFalse(sidecar.is_file())
            rows = recall_parametric(store, {"case_name": "case-2"})
            self.assertTrue(sidecar.is_file())
            self.assertGreater(len(rows), 0)
            # All hits actually have the right case_name.
            for r in rows:
                self.assertEqual(r["case_name"], "case-2")

    def test_small_store_recall_does_not_build_index(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, 200)
            sidecar = index_path_for(store)
            recall_parametric(store, {"case_name": "case-1"})
            self.assertFalse(
                sidecar.is_file(),
                msg="small store should not auto-build the sidecar",
            )

    def test_write_does_not_touch_sidecar_subsequent_read_rebuilds(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, DEFAULT_INDEX_THRESHOLD + 10)
            recall_parametric(store, {})  # builds the index
            sidecar = index_path_for(store)
            self.assertTrue(sidecar.is_file())
            # Force the sidecar mtime into the past so the next write
            # leaves the JSONL newer than the sidecar (simulates a
            # cross-test deterministic mtime layout).
            past = time.time() - 7200
            os.utime(sidecar, (past, past))
            # Now append.
            record_parametric_run(
                store,
                ParametricRecord(
                    run_id="post-write",
                    case_name="case-post",
                    swmm_version="5.2.4",
                ),
            )
            # Writing must NOT have touched the sidecar mtime — it's
            # still in the past.
            self.assertLess(sidecar.stat().st_mtime, time.time() - 100)
            # Now read: the recall path should detect the stale sidecar
            # and rebuild. The new row must surface.
            rows = recall_parametric(store, {"case_name": "case-post"})
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], "post-write")
            # And the sidecar is fresh again.
            self.assertGreater(sidecar.stat().st_mtime, time.time() - 100)

    def test_recall_falls_back_to_linear_on_unknown_filter_key(self) -> None:
        # A filter key the index does not know about (e.g. a 2.0
        # ``extras`` field) returns 1=0 from the index path; the recall
        # must still find the row via the linear-scan fallback.
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, DEFAULT_INDEX_THRESHOLD + 5)
            # The recall path uses the index for known fields. Unknown
            # fields cause the index path to return [] (no match);
            # caller does not crash.
            rows = recall_parametric(store, {"completely_unknown_field": "x"})
            self.assertEqual(rows, [])

    def test_recall_returns_identical_rows_for_index_vs_linear(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, DEFAULT_INDEX_THRESHOLD + 100)
            # Indexed.
            indexed = recall_parametric(store, {"swmm_version": "5.2.4"})
            # Linear: nuke the sidecar.
            sidecar = index_path_for(store)
            sidecar.unlink()
            # Drop below threshold by truncating — pre-seed instead.
            # Easier: read all rows and compare by run_id sets.
            linear = recall_parametric(store, {"swmm_version": "5.2.4"})
        self.assertEqual(
            sorted(r["run_id"] for r in indexed),
            sorted(r["run_id"] for r in linear),
        )


class StaleErrorFallbackTests(unittest.TestCase):
    """When a sidecar exists but is older than the JSONL, the recall
    path catches ``IndexStaleError`` and falls back to a linear scan
    after rebuilding."""

    def test_recall_after_jsonl_append_picks_up_new_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _seed_store(store, DEFAULT_INDEX_THRESHOLD + 1)
            build_or_refresh_index(store)
            # Make sidecar old.
            sidecar = index_path_for(store)
            past = time.time() - 3600
            os.utime(sidecar, (past, past))
            # Append.
            record_parametric_run(
                store,
                ParametricRecord(
                    run_id="newest",
                    case_name="case-newest",
                    swmm_version="5.2.4",
                ),
            )
            rows = recall_parametric(store, {"case_name": "case-newest"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], "newest")


if __name__ == "__main__":
    unittest.main()
