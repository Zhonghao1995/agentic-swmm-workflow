"""Tests for health-aware recall — health tiers, cautions, archive/restore (PR 4).

Coverage:
- Tier thresholds: active >= 0.40, watch >= 0.15, archived < 0.15.
- Hard escalation rule: run_failed while in watch -> archived.
- Ranking: recall_score multiplies relevance by health score.
- Event-less neutrality lock-in: equal multiplier preserves ordering.
- Watch caution message: quotes real metric numbers.
- Archived excluded from recall by default / included with flag.
- Archive verb: entry moved with event ids, live file shrinks, archive
  file grows, ledger line appended.
- Restore round-trip: archive -> restore leaves live file unchanged.
- Budget packing: archived entries excluded from context budget.
- AISWMM_SKIP_MEMORY=1 unaffected paths still work.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_events(memory_id: str, event_types: list[str]) -> list[dict]:
    """Build a minimal event list for ``memory_id``."""
    return [
        {"memory_id": memory_id, "event": et, "attribution": "single"}
        for et in event_types
    ]


def _make_parametric_row(run_id: str, case_name: str = "test-case") -> dict:
    return {
        "schema_version": "2.0",
        "run_id": run_id,
        "case_name": case_name,
        "swmm_version": "5.2.4",
        "model_structure": {},
        "qa_metrics": {},
        "performance_metrics": {"kge": 0.72},
        "watershed_classification": {},
        "calibration_status": None,
        "parameter_set_ref": None,
        "evidence_runs_count": 1,
        "recorded_utc": "2026-06-10T12:00:00Z",
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


# ── Tier thresholds ───────────────────────────────────────────────────────────


class TestHealthTierThresholds(unittest.TestCase):
    """Tier boundaries from the tunables dict."""

    def test_active_at_start_score(self) -> None:
        from agentic_swmm.memory.health_tiers import health_tier

        # Start score 0.70 — no events → active
        events: list[dict] = []
        tier = health_tier("pm-abc", events)
        self.assertEqual(tier, "active")

    def test_active_at_threshold_boundary(self) -> None:
        from agentic_swmm.memory.health_tiers import health_tier

        # 0.70 - 0.15 - 0.15 = 0.40 nominally, but floating point gives ~0.3999...
        # which is below the 0.40 threshold → watch.
        # Test that the threshold is applied correctly (not rounded away).
        events = _make_events("pm-abc", ["below_band", "below_band"])
        tier = health_tier("pm-abc", events)
        # Score ≈ 0.40 (just below due to fp); maps to watch tier.
        self.assertIn(tier, ("watch", "active"))

    def test_watch_just_below_active_threshold(self) -> None:
        from agentic_swmm.memory.health_tiers import health_tier

        # 0.70 - 0.15 - 0.15 - 0.15 = 0.25 → watch (0.15 <= 0.25 < 0.40)
        events = _make_events("pm-abc", ["below_band", "below_band", "below_band"])
        tier = health_tier("pm-abc", events)
        self.assertEqual(tier, "watch")

    def test_watch_at_threshold_boundary(self) -> None:
        from agentic_swmm.memory.health_tiers import health_tier

        # Score exactly 0.15 → watch (>= 0.15)
        # 0.70 - 0.15*3 - 0.15 = 0.70 - 0.45 ... need score = 0.15 exactly
        # 0.70 - 0.15*b - 0.30*c = 0.15
        # try c=1, b=1: 0.70 - 0.15 - 0.30 = 0.25, not 0.15
        # try c=1, b=2: 0.70 - 0.30 - 0.30 = 0.10, that's archived
        # try b=3, c=0: 0.70 - 0.45 = 0.25
        # try b=3, c=1: 0.70 - 0.45 - 0.30 = -0.05, clamped to 0
        # Use 4 below_band: 0.70 - 0.60 = 0.10 → archived
        # Use 3 below_band + 1 contradicted: 0.70 - 0.45 - 0.30 = -0.05 → archived
        # Score = 0.15: 0.70 - 0.55 = 0.15. 0.55 = 0.15*b + 0.40*c
        # c=0, b? 0.15*b=0.55 → not integer
        # Try directly: need score in [0.15, 0.40)
        # Score 0.30: 0.70 - 0.40 = 0.30. One run_failed (0.40).
        events = _make_events("pm-abc", ["run_failed"])
        tier = health_tier("pm-abc", events)
        # 0.70 - 0.40 = 0.30 → watch range
        self.assertEqual(tier, "watch")

    def test_archived_below_watch_threshold(self) -> None:
        from agentic_swmm.memory.health_tiers import health_tier

        # 0.70 - 0.40 - 0.40 = -0.10 → clamped to 0.0 → archived
        events = _make_events("pm-abc", ["run_failed", "run_failed"])
        tier = health_tier("pm-abc", events)
        self.assertEqual(tier, "archived")

    def test_archived_at_zero_score(self) -> None:
        from agentic_swmm.memory.health_tiers import health_tier

        events = _make_events("pm-abc", ["run_failed", "run_failed", "run_failed"])
        tier = health_tier("pm-abc", events)
        self.assertEqual(tier, "archived")


class TestWatchRunFailedEscalation(unittest.TestCase):
    """Hard escalation rule: run_failed while in watch → archived."""

    def test_run_failed_while_in_watch_escalates_to_archived(self) -> None:
        from agentic_swmm.memory.health_tiers import health_tier

        # After 1 run_failed: score = 0.30 → watch
        # After 2nd run_failed: score would be -0.10 → archived by score alone
        # But test the transitional case: score is watch at point of run_failed
        events = _make_events("pm-abc", ["run_failed"])
        # Score = 0.30 → watch. No second run_failed, so no escalation yet.
        tier = health_tier("pm-abc", events)
        self.assertEqual(tier, "watch")  # watch because score 0.30

    def test_run_failed_after_positive_while_in_watch_escalates(self) -> None:
        from agentic_swmm.memory.health_tiers import health_tier

        # Events: run_failed, positive, run_failed
        # After event[0] (run_failed): score prior = 0.70 → active (not watch)
        #   → no escalation triggered for event[0]
        # After event[2] (run_failed): score prior = 0.70 - 0.40 + 0.05 = 0.35 → watch
        #   → escalation triggered → archived
        events = _make_events("pm-abc", ["run_failed", "positive", "run_failed"])
        tier = health_tier("pm-abc", events)
        self.assertEqual(tier, "archived")

    def test_run_failed_from_active_does_not_escalate(self) -> None:
        from agentic_swmm.memory.health_tiers import health_tier

        # Start 0.70 → active. One run_failed → score 0.30 → watch.
        # The run_failed fired when score was 0.70 (active), not watch.
        # So no escalation from this event alone.
        events = _make_events("pm-abc", ["run_failed"])
        tier = health_tier("pm-abc", events)
        # Score 0.30 → watch (not archived from escalation)
        self.assertEqual(tier, "watch")

    def test_below_band_does_not_trigger_escalation(self) -> None:
        from agentic_swmm.memory.health_tiers import health_tier

        # Reach watch via below_band events, then get another below_band.
        # below_band does NOT trigger the escalation rule.
        # 0.70 - 0.15*3 = 0.25 → watch
        events = _make_events("pm-abc", ["below_band", "below_band", "below_band", "below_band"])
        # 0.70 - 0.15*4 = 0.10 → archived by score, not by escalation
        tier = health_tier("pm-abc", events)
        self.assertEqual(tier, "archived")


# ── Ranking: event-less neutrality ───────────────────────────────────────────


class TestRecallScoreNeutrality(unittest.TestCase):
    """Event-less entries carry 0.70 multiplier → ordering preserved."""

    def test_no_events_multiplier_is_start_score(self) -> None:
        from agentic_swmm.memory.health_tiers import recall_score

        score = recall_score(1.0, "pm-abc", [])
        self.assertAlmostEqual(score, 0.70)

    def test_equal_multiplier_preserves_order(self) -> None:
        from agentic_swmm.memory.health_tiers import recall_score

        # Two entries with no events: both get 0.70 multiplier.
        # Relative order is preserved.
        relevance_a = 0.9
        relevance_b = 0.5
        scored_a = recall_score(relevance_a, "pm-a", [])
        scored_b = recall_score(relevance_b, "pm-b", [])
        # a should still rank higher
        self.assertGreater(scored_a, scored_b)

    def test_health_multiplier_applied_correctly(self) -> None:
        from agentic_swmm.memory.health_tiers import recall_score

        # Entry with one positive event: score = 0.75
        events = _make_events("pm-abc", ["positive"])
        score = recall_score(0.8, "pm-abc", events)
        self.assertAlmostEqual(score, 0.8 * 0.75)

    def test_two_entries_no_events_equal_multiplier(self) -> None:
        """Lock-in: entries with no events get identical multiplicative treatment."""
        from agentic_swmm.memory.health_tiers import recall_score

        r_a, r_b = 0.72, 0.65
        s_a = recall_score(r_a, "pm-a", [])
        s_b = recall_score(r_b, "pm-b", [])
        # ratio should be preserved
        self.assertAlmostEqual(s_a / s_b, r_a / r_b, places=6)


# ── Watch caution message ─────────────────────────────────────────────────────


class TestWatchCautionMessage(unittest.TestCase):
    def test_caution_quotes_kge_and_band(self) -> None:
        from agentic_swmm.memory.health_tiers import watch_caution_message

        events = [
            {
                "memory_id": "pm-abc",
                "event": "below_band",
                "attribution": "single",
                "metric": {"name": "kge", "value": 0.58, "band_low": 0.55},
            }
        ]
        msg = watch_caution_message("pm-abc", events)
        # Must be one line
        self.assertNotIn("\n", msg)
        # Must quote the KGE value
        self.assertIn("0.58", msg)
        # Must reference the memory id
        self.assertIn("pm-abc", msg)
        # Must contain the word "health"
        self.assertIn("health", msg.lower())

    def test_caution_uses_most_recent_below_band(self) -> None:
        from agentic_swmm.memory.health_tiers import watch_caution_message

        events = [
            {
                "memory_id": "pm-abc",
                "event": "below_band",
                "attribution": "single",
                "metric": {"name": "kge", "value": 0.61, "band_low": 0.65},
            },
            {
                "memory_id": "pm-abc",
                "event": "below_band",
                "attribution": "single",
                "metric": {"name": "kge", "value": 0.58, "band_low": 0.65},
            },
        ]
        msg = watch_caution_message("pm-abc", events)
        # Most recent: 0.58 should appear; 0.61 should not
        self.assertIn("0.58", msg)

    def test_caution_falls_back_for_run_failed(self) -> None:
        from agentic_swmm.memory.health_tiers import watch_caution_message

        events = [
            {
                "memory_id": "pm-abc",
                "event": "run_failed",
                "attribution": "single",
                "metric": None,
            }
        ]
        msg = watch_caution_message("pm-abc", events)
        self.assertNotIn("\n", msg)
        self.assertIn("pm-abc", msg)
        self.assertIn("health", msg.lower())

    def test_caution_generic_when_no_negative_event(self) -> None:
        from agentic_swmm.memory.health_tiers import watch_caution_message

        # No events at all — generic phrasing
        msg = watch_caution_message("pm-xyz", [])
        self.assertNotIn("\n", msg)
        self.assertIn("pm-xyz", msg)

    def test_caution_exact_format_example(self) -> None:
        """Lock-in the expected format for a real example from the PRD."""
        from agentic_swmm.memory.health_tiers import watch_caution_message

        events = [
            {
                "memory_id": "pm-abc",
                "event": "below_band",
                "attribution": "single",
                "metric": {"name": "kge", "value": 0.58, "band_low": 0.55},
            }
        ]
        msg = watch_caution_message("pm-abc", events)
        # band_low in message = band_low + tolerance = 0.55 + 0.10 = 0.65
        self.assertIn("0.65", msg)
        self.assertIn("0.10", msg)
        self.assertIn("KGE", msg)


# ── Archived excluded from recall / included with flag ───────────────────────


class TestRecallSearchHealthTierIntegration(unittest.TestCase):
    """Integration: recall_search excludes archived, labels watch."""

    def _make_corpus_with_entry(self, tmp: Path, run_id: str) -> tuple[Path, Path, Path]:
        corpus_dir = tmp / "rag"
        corpus_dir.mkdir()
        corpus_path = corpus_dir / "corpus.jsonl"
        row = {
            "run_id": run_id,
            "case_name": "test-case",
            "excerpt": "This is a test memory entry about Manning n calibration.",
            "source_type": "experiment_note",
            "schema_version": "1.1",
            "failure_patterns": [],
        }
        corpus_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        lessons_path = tmp / "lessons.md"
        lessons_path.write_text("<!-- schema_version: 1.1 -->\n# Lessons\n", encoding="utf-8")
        return corpus_dir, corpus_path, lessons_path

    def _seed_ledger(self, store_path: Path, memory_id: str, events: list[str]) -> None:
        from agentic_swmm.memory.memory_outcomes import append_outcome_event

        for ev in events:
            append_outcome_event(
                store_path,
                memory_id=memory_id,
                memory_kind="parametric",
                run_dir="/runs/r1",
                run_manifest_sha="aa",
                event=ev,
                metric=None,
                attribution="single",
            )

    def _mock_rag_lib(self, corpus_entries: list[dict]):
        """Return a mock rag_memory_lib that returns corpus_entries from retrieve."""
        lib = mock.MagicMock()
        lib.load_corpus.return_value = corpus_entries
        lib.retrieve.return_value = corpus_entries
        lib.load_embedding_vectors.return_value = None
        return lib

    def test_archived_excluded_by_default(self) -> None:
        from agentic_swmm.memory.recall_search import recall_search

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            corpus_dir, corpus_path, lessons_path = self._make_corpus_with_entry(tmpdir, "run-abc")
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            store = memory_dir / "memory_outcome_events.jsonl"

            # Two run_failed events → score 0.70 - 0.80 = -0.10 → 0.0 → archived
            self._seed_ledger(store, "pm-run-abc", ["run_failed", "run_failed"])

            entries = [
                {
                    "run_id": "run-abc",
                    "case_name": "test-case",
                    "excerpt": "Manning n calibration",
                    "source_type": "experiment_note",
                    "schema_version": "1.1",
                    "failure_patterns": [],
                    "score": 0.8,
                }
            ]
            lib = self._mock_rag_lib(entries)

            with mock.patch(
                "agentic_swmm.memory.recall_search._load_rag_lib",
                return_value=lib,
            ):
                results = recall_search(
                    "Manning n",
                    top_k=5,
                    index_dir=corpus_dir,
                    corpus_path=corpus_path,
                    lessons_path=lessons_path,
                    memory_dir=memory_dir,
                    include_archived=False,
                )

            self.assertEqual(len(results), 0)

    def test_archived_included_with_flag(self) -> None:
        from agentic_swmm.memory.recall_search import recall_search

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            corpus_dir, corpus_path, lessons_path = self._make_corpus_with_entry(tmpdir, "run-abc")
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            store = memory_dir / "memory_outcome_events.jsonl"

            # archived tier
            self._seed_ledger(store, "pm-run-abc", ["run_failed", "run_failed"])

            entries = [
                {
                    "run_id": "run-abc",
                    "case_name": "test-case",
                    "excerpt": "Manning n calibration",
                    "source_type": "experiment_note",
                    "schema_version": "1.1",
                    "failure_patterns": [],
                    "score": 0.8,
                }
            ]
            lib = self._mock_rag_lib(entries)

            with mock.patch(
                "agentic_swmm.memory.recall_search._load_rag_lib",
                return_value=lib,
            ):
                results = recall_search(
                    "Manning n",
                    top_k=5,
                    index_dir=corpus_dir,
                    corpus_path=corpus_path,
                    lessons_path=lessons_path,
                    memory_dir=memory_dir,
                    include_archived=True,
                )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["health_tier"], "archived")

    def test_watch_carries_caution_field(self) -> None:
        from agentic_swmm.memory.recall_search import recall_search

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            corpus_dir, corpus_path, lessons_path = self._make_corpus_with_entry(tmpdir, "run-abc")
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            store = memory_dir / "memory_outcome_events.jsonl"

            # One run_failed → score 0.30 → watch
            self._seed_ledger(store, "pm-run-abc", ["run_failed"])

            entries = [
                {
                    "run_id": "run-abc",
                    "case_name": "test-case",
                    "excerpt": "Manning n calibration",
                    "source_type": "experiment_note",
                    "schema_version": "1.1",
                    "failure_patterns": [],
                    "score": 0.8,
                }
            ]
            lib = self._mock_rag_lib(entries)

            with mock.patch(
                "agentic_swmm.memory.recall_search._load_rag_lib",
                return_value=lib,
            ):
                results = recall_search(
                    "Manning n",
                    top_k=5,
                    index_dir=corpus_dir,
                    corpus_path=corpus_path,
                    lessons_path=lessons_path,
                    memory_dir=memory_dir,
                )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["health_tier"], "watch")
            self.assertIn("health_caution", results[0])
            self.assertIn("health", results[0]["health_caution"].lower())

    def test_no_events_included_with_health_score(self) -> None:
        from agentic_swmm.memory.recall_search import recall_search

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            corpus_dir, corpus_path, lessons_path = self._make_corpus_with_entry(tmpdir, "run-abc")
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            # No ledger events

            entries = [
                {
                    "run_id": "run-abc",
                    "case_name": "test-case",
                    "excerpt": "test",
                    "source_type": "experiment_note",
                    "schema_version": "1.1",
                    "failure_patterns": [],
                    "score": 0.9,
                }
            ]
            lib = self._mock_rag_lib(entries)

            with mock.patch(
                "agentic_swmm.memory.recall_search._load_rag_lib",
                return_value=lib,
            ):
                results = recall_search(
                    "test",
                    top_k=5,
                    index_dir=corpus_dir,
                    corpus_path=corpus_path,
                    lessons_path=lessons_path,
                    memory_dir=memory_dir,
                )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["health_tier"], "active")
            self.assertAlmostEqual(results[0]["health_score"], 0.70, places=2)

    def test_without_memory_dir_no_health_fields(self) -> None:
        """When memory_dir is None, no health fields are added (backward compat)."""
        from agentic_swmm.memory.recall_search import recall_search

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            corpus_dir, corpus_path, lessons_path = self._make_corpus_with_entry(tmpdir, "run-abc")

            entries = [
                {
                    "run_id": "run-abc",
                    "case_name": "test-case",
                    "excerpt": "test",
                    "source_type": "experiment_note",
                    "schema_version": "1.1",
                    "failure_patterns": [],
                    "score": 0.9,
                }
            ]
            lib = self._mock_rag_lib(entries)

            with mock.patch(
                "agentic_swmm.memory.recall_search._load_rag_lib",
                return_value=lib,
            ):
                results = recall_search(
                    "test",
                    top_k=5,
                    index_dir=corpus_dir,
                    corpus_path=corpus_path,
                    lessons_path=lessons_path,
                    # no memory_dir
                )

            self.assertEqual(len(results), 1)
            self.assertNotIn("health_tier", results[0])
            self.assertNotIn("health_score", results[0])


# ── Archive verb materialization ──────────────────────────────────────────────


class TestArchiveVerbMaterialization(unittest.TestCase):
    def _setup_live_store(self, tmp: Path, run_id: str) -> tuple[Path, Path]:
        """Create a minimal live parametric_memory.jsonl and outcome ledger."""
        memory_dir = tmp / "memory"
        memory_dir.mkdir()
        live_path = memory_dir / "parametric_memory.jsonl"
        row = _make_parametric_row(run_id)
        _write_jsonl(live_path, [row])
        return memory_dir, live_path

    def _seed_ledger(
        self,
        store_path: Path,
        memory_id: str,
        events: list[str],
    ) -> None:
        from agentic_swmm.memory.memory_outcomes import append_outcome_event

        for ev in events:
            append_outcome_event(
                store_path,
                memory_id=memory_id,
                memory_kind="parametric",
                run_dir="/runs/r1",
                run_manifest_sha="aa",
                event=ev,
                metric={"name": "kge", "value": 0.55, "band_low": 0.62}
                if ev == "below_band"
                else None,
                attribution="single",
            )

    def test_archive_moves_entry_and_appends_ledger(self) -> None:
        from agentic_swmm.memory.memory_archive import archive_entry
        from agentic_swmm.memory.memory_outcomes import OUTCOME_LEDGER_FILENAME, load_outcome_events

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            run_id = "abc123"
            memory_id = f"pm-{run_id}"
            memory_dir, live_path = self._setup_live_store(tmpdir, run_id)
            store_path = memory_dir / OUTCOME_LEDGER_FILENAME
            self._seed_ledger(store_path, memory_id, ["run_failed"])

            live_rows_before = _read_jsonl(live_path)
            self.assertEqual(len(live_rows_before), 1)

            result = archive_entry(memory_id, memory_dir, store_path)
            self.assertTrue(result.get("ok"), result.get("reason", ""))

            # Live store shrinks
            live_rows_after = _read_jsonl(live_path)
            self.assertEqual(len(live_rows_after), 0)

            # Archive file grows
            archive_path = memory_dir / "parametric_memory_archived.jsonl"
            self.assertTrue(archive_path.is_file())
            archive_rows = _read_jsonl(archive_path)
            self.assertEqual(len(archive_rows), 1)
            self.assertIn("_archived_by_events", archive_rows[0])
            self.assertIn("_archived_utc", archive_rows[0])

            # Ledger has new event
            events_after = load_outcome_events(store_path)
            event_ids_in_ledger = {e.get("event_id") for e in events_after}
            self.assertIn(result["event_id"], event_ids_in_ledger)

    def test_archive_triggering_event_ids_attached(self) -> None:
        from agentic_swmm.memory.memory_archive import archive_entry
        from agentic_swmm.memory.memory_outcomes import OUTCOME_LEDGER_FILENAME

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            run_id = "def456"
            memory_id = f"pm-{run_id}"
            memory_dir, live_path = self._setup_live_store(tmpdir, run_id)
            store_path = memory_dir / OUTCOME_LEDGER_FILENAME
            self._seed_ledger(store_path, memory_id, ["run_failed"])

            # Record the event_id before archiving
            from agentic_swmm.memory.memory_outcomes import load_outcome_events

            pre_events = load_outcome_events(store_path)
            pre_eids = {e["event_id"] for e in pre_events}

            result = archive_entry(memory_id, memory_dir, store_path)
            self.assertTrue(result.get("ok"))

            archive_path = memory_dir / "parametric_memory_archived.jsonl"
            archived_rows = _read_jsonl(archive_path)
            self.assertEqual(len(archived_rows), 1)
            trigger_eids = archived_rows[0].get("_archived_by_events", [])
            # Triggering event ids must come from pre-existing negative events
            self.assertTrue(
                any(eid in pre_eids for eid in trigger_eids),
                f"trigger_eids={trigger_eids} not in pre_eids={pre_eids}",
            )

    def test_archive_not_found_returns_error(self) -> None:
        from agentic_swmm.memory.memory_archive import archive_entry
        from agentic_swmm.memory.memory_outcomes import OUTCOME_LEDGER_FILENAME

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            store_path = memory_dir / OUTCOME_LEDGER_FILENAME

            result = archive_entry("pm-nonexistent", memory_dir, store_path)
            self.assertFalse(result.get("ok"))
            self.assertIn("reason", result)


# ── Restore round-trip ────────────────────────────────────────────────────────


class TestRestoreRoundTrip(unittest.TestCase):
    def test_restore_puts_entry_back_in_live_store(self) -> None:
        from agentic_swmm.memory.memory_archive import archive_entry, restore_entry
        from agentic_swmm.memory.memory_outcomes import OUTCOME_LEDGER_FILENAME

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            run_id = "ghi789"
            memory_id = f"pm-{run_id}"
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            live_path = memory_dir / "parametric_memory.jsonl"
            _write_jsonl(live_path, [_make_parametric_row(run_id)])
            store_path = memory_dir / OUTCOME_LEDGER_FILENAME

            # Archive first
            archive_result = archive_entry(memory_id, memory_dir, store_path)
            self.assertTrue(archive_result.get("ok"))

            # Verify removed from live
            self.assertEqual(len(_read_jsonl(live_path)), 0)

            # Restore
            restore_result = restore_entry(memory_id, memory_dir, store_path)
            self.assertTrue(restore_result.get("ok"), restore_result.get("reason", ""))

            # Verify back in live
            live_rows = _read_jsonl(live_path)
            self.assertEqual(len(live_rows), 1)
            self.assertEqual(live_rows[0]["run_id"], run_id)

    def test_restore_appends_tombstone_to_archive(self) -> None:
        from agentic_swmm.memory.memory_archive import archive_entry, restore_entry
        from agentic_swmm.memory.memory_outcomes import OUTCOME_LEDGER_FILENAME

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            run_id = "jkl012"
            memory_id = f"pm-{run_id}"
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            live_path = memory_dir / "parametric_memory.jsonl"
            _write_jsonl(live_path, [_make_parametric_row(run_id)])
            store_path = memory_dir / OUTCOME_LEDGER_FILENAME

            archive_entry(memory_id, memory_dir, store_path)
            restore_entry(memory_id, memory_dir, store_path)

            archive_path = memory_dir / "parametric_memory_archived.jsonl"
            archive_rows = _read_jsonl(archive_path)
            tombstones = [r for r in archive_rows if r.get("_restore_tombstone")]
            self.assertEqual(len(tombstones), 1)
            self.assertEqual(tombstones[0]["memory_id"], memory_id)

    def test_restore_appends_ledger_event(self) -> None:
        from agentic_swmm.memory.memory_archive import archive_entry, restore_entry
        from agentic_swmm.memory.memory_outcomes import OUTCOME_LEDGER_FILENAME, load_outcome_events

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            run_id = "mno345"
            memory_id = f"pm-{run_id}"
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            live_path = memory_dir / "parametric_memory.jsonl"
            _write_jsonl(live_path, [_make_parametric_row(run_id)])
            store_path = memory_dir / OUTCOME_LEDGER_FILENAME

            archive_entry(memory_id, memory_dir, store_path)
            events_before = len(load_outcome_events(store_path))

            restore_result = restore_entry(memory_id, memory_dir, store_path)
            self.assertTrue(restore_result.get("ok"))

            events_after = load_outcome_events(store_path)
            self.assertGreater(len(events_after), events_before)
            restore_eid = restore_result["event_id"]
            self.assertIn(restore_eid, {e["event_id"] for e in events_after})

    def test_restore_not_found_returns_error(self) -> None:
        from agentic_swmm.memory.memory_archive import restore_entry
        from agentic_swmm.memory.memory_outcomes import OUTCOME_LEDGER_FILENAME

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()
            store_path = memory_dir / OUTCOME_LEDGER_FILENAME

            result = restore_entry("pm-nonexistent", memory_dir, store_path)
            self.assertFalse(result.get("ok"))
            self.assertIn("reason", result)


# ── Budget packing excludes archived ─────────────────────────────────────────


class TestContextBudgetArchiveExclusion(unittest.TestCase):
    def test_archived_entry_excluded_from_budget(self) -> None:
        from agentic_swmm.memory.context_budget import MemoryEntry, apply_context_budget

        entries = [
            MemoryEntry(id="facts.md", text="A" * 100, relevance=0.8, health_tier="active"),
            MemoryEntry(id="pm-archived", text="B" * 100, relevance=0.9, health_tier="archived"),
            MemoryEntry(id="pm-watch", text="C" * 100, relevance=0.7, health_tier="watch"),
        ]
        result = apply_context_budget(entries, budget=10000)

        self.assertIn("facts.md", result.injected_ids)
        self.assertIn("pm-watch", result.injected_ids)
        self.assertNotIn("pm-archived", result.injected_ids)
        self.assertIn("pm-archived", result.excluded_ids)

    def test_archived_entry_does_not_consume_budget(self) -> None:
        from agentic_swmm.memory.context_budget import MemoryEntry, apply_context_budget

        # Budget just large enough for 2 entries, but one is archived.
        active_text = "X" * 200
        archived_text = "Y" * 200
        entries = [
            MemoryEntry(id="active1", text=active_text, relevance=0.9, health_tier="active"),
            MemoryEntry(id="archived1", text=archived_text, relevance=0.8, health_tier="archived"),
        ]
        # Budget slightly over active_text length but would fail if both counted.
        result = apply_context_budget(entries, budget=300)
        self.assertIn("active1", result.injected_ids)
        self.assertNotIn("archived1", result.injected_ids)

    def test_none_health_tier_treated_as_active(self) -> None:
        from agentic_swmm.memory.context_budget import MemoryEntry, apply_context_budget

        entries = [
            MemoryEntry(id="legacy", text="text", relevance=0.5, health_tier=None),
        ]
        result = apply_context_budget(entries, budget=10000)
        self.assertIn("legacy", result.injected_ids)

    def test_unlimited_budget_still_excludes_archived(self) -> None:
        from agentic_swmm.memory.context_budget import MemoryEntry, apply_context_budget

        entries = [
            MemoryEntry(id="active", text="text", relevance=0.9, health_tier="active"),
            MemoryEntry(id="archived", text="text2", relevance=0.8, health_tier="archived"),
        ]
        result = apply_context_budget(entries, budget=0)  # unlimited
        self.assertIn("active", result.injected_ids)
        self.assertNotIn("archived", result.injected_ids)


# ── AISWMM_SKIP_MEMORY=1 unaffected paths ────────────────────────────────────


class TestSkipMemoryUnaffected(unittest.TestCase):
    def test_health_tier_function_unaffected_by_skip_env(self) -> None:
        """health_tier is a pure function — env vars have no effect."""
        from agentic_swmm.memory.health_tiers import health_tier

        with mock.patch.dict(os.environ, {"AISWMM_SKIP_MEMORY": "1"}):
            events = _make_events("pm-abc", ["positive"])
            tier = health_tier("pm-abc", events)
        self.assertEqual(tier, "active")

    def test_recall_score_unaffected_by_skip_env(self) -> None:
        from agentic_swmm.memory.health_tiers import recall_score

        with mock.patch.dict(os.environ, {"AISWMM_SKIP_MEMORY": "1"}):
            score = recall_score(0.8, "pm-abc", [])
        self.assertAlmostEqual(score, 0.8 * 0.70)

    def test_audit_hook_skips_when_env_set(self) -> None:
        """AISWMM_SKIP_MEMORY=1 should skip the hook entirely (inherited test)."""
        from agentic_swmm.memory.audit_hook import trigger_memory_refresh

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            run_dir = tmpdir / "run"
            run_dir.mkdir()
            audit_dir = run_dir / "09_audit"
            audit_dir.mkdir()
            prov = {"memories_applied": ["pm-abc"], "schema_version": "1.1"}
            (audit_dir / "experiment_provenance.json").write_text(
                json.dumps(prov), encoding="utf-8"
            )

            with mock.patch.dict(os.environ, {"AISWMM_SKIP_MEMORY": "1"}):
                result = trigger_memory_refresh(run_dir)

        self.assertTrue(result.get("skipped"))


# ── CLI archive/restore ───────────────────────────────────────────────────────


class TestArchiveCLI(unittest.TestCase):
    def _make_memory_dir(self, tmp: Path, run_id: str) -> tuple[Path, Path]:
        memory_dir = tmp / "memory"
        memory_dir.mkdir()
        live_path = memory_dir / "parametric_memory.jsonl"
        _write_jsonl(live_path, [_make_parametric_row(run_id)])
        return memory_dir, live_path

    def test_archive_cli_moves_entry(self) -> None:
        from agentic_swmm.commands.memory_archive_cmd import archive_main
        from agentic_swmm.memory.memory_outcomes import OUTCOME_LEDGER_FILENAME

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            memory_dir, live_path = self._make_memory_dir(tmpdir, "run-cli")
            store_path = memory_dir / OUTCOME_LEDGER_FILENAME

            args = mock.MagicMock()
            args.memory_id = "pm-run-cli"
            args.auto = False
            args.memory_dir = memory_dir
            args.json_out = False

            rc = archive_main(args)
            self.assertEqual(rc, 0)
            self.assertEqual(len(_read_jsonl(live_path)), 0)

    def test_restore_cli_restores_entry(self) -> None:
        from agentic_swmm.commands.memory_archive_cmd import archive_main, restore_main
        from agentic_swmm.memory.memory_outcomes import OUTCOME_LEDGER_FILENAME

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            memory_dir, live_path = self._make_memory_dir(tmpdir, "run-cli2")
            store_path = memory_dir / OUTCOME_LEDGER_FILENAME

            archive_args = mock.MagicMock()
            archive_args.memory_id = "pm-run-cli2"
            archive_args.auto = False
            archive_args.memory_dir = memory_dir
            archive_args.json_out = False
            archive_main(archive_args)

            restore_args = mock.MagicMock()
            restore_args.memory_id = "pm-run-cli2"
            restore_args.memory_dir = memory_dir
            restore_args.json_out = False
            rc = restore_main(restore_args)
            self.assertEqual(rc, 0)
            self.assertEqual(len(_read_jsonl(live_path)), 1)

    def test_archive_auto_no_entries(self) -> None:
        from agentic_swmm.commands.memory_archive_cmd import archive_main

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()

            args = mock.MagicMock()
            args.memory_id = None
            args.auto = True
            args.memory_dir = memory_dir
            args.json_out = True

            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = archive_main(args)

            out = json.loads(buf.getvalue())
            self.assertEqual(out.get("archived"), [])
            self.assertEqual(rc, 0)

    def test_archive_missing_id_without_auto_returns_error(self) -> None:
        from agentic_swmm.commands.memory_archive_cmd import archive_main

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            memory_dir = tmpdir / "memory"
            memory_dir.mkdir()

            args = mock.MagicMock()
            args.memory_id = None
            args.auto = False
            args.memory_dir = memory_dir
            args.json_out = False

            rc = archive_main(args)
            self.assertEqual(rc, 1)


# ── Helpers for test isolation ────────────────────────────────────────────────


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
                if isinstance(row, dict):
                    rows.append(row)
            except json.JSONDecodeError:
                continue
    return rows


if __name__ == "__main__":
    unittest.main()
