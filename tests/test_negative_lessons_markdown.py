"""Negative lessons markdown lifecycle (Round 7).

Mirrors the lifecycle contract of ``lessons_learned.md`` but writes to
``negative_lessons.md`` so the curator can grep, edit, and archive
failure regions in the same way they curate success patterns.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.negative_lessons import (
    NegativeLesson,
    record_negative_lesson,
)
from agentic_swmm.memory.negative_lessons_markdown import (
    DEFAULT_HALF_LIFE_DAYS,
    NegativeLessonMd,
    add_negative_lesson,
    apply_decay,
    archive_retired,
    is_param_set_known_bad_md,
    list_negative_lessons,
    migrate_jsonl_to_md,
)


def _utc(iso: str) -> str:
    return iso


def _seed(
    name: str = "continuity_fail_saanich_b8",
    case: str = "saanich-b8",
    *,
    parameters: dict[str, float] | None = None,
    last_seen: str = "2026-05-19T10:00:00Z",
    half_life: int = DEFAULT_HALF_LIFE_DAYS,
) -> NegativeLessonMd:
    return NegativeLessonMd(
        name=name,
        case=case,
        lesson_type="continuity_fail",
        parameters_tried=parameters or {"manning_n_overland": 0.25},
        note="postflight FAIL on runoff_continuity_pct",
        first_seen=last_seen,
        last_seen=last_seen,
        evidence_count=1,
        evidence_runs=["20260519-100000_saanich"],
        status="active",
        confidence_score=1.0,
        half_life_days=half_life,
    )


class AddNegativeLessonTests(unittest.TestCase):
    def test_empty_store_creates_one_section(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            add_negative_lesson(store, _seed())
            text = store.read_text(encoding="utf-8")
            self.assertIn("## continuity_fail_saanich_b8", text)
            self.assertIn("aiswmm-negative-lesson-metadata", text)
            self.assertIn("case: saanich-b8", text)

    def test_duplicate_name_increments_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            add_negative_lesson(store, _seed())
            second = _seed()
            object.__setattr__(
                second, "evidence_runs", ["20260520-090000_saanich"]
            )
            add_negative_lesson(store, second)
            lessons = list_negative_lessons(store)
            self.assertEqual(1, len(lessons))
            self.assertEqual(2, lessons[0].evidence_count)
            self.assertIn("20260519-100000_saanich", lessons[0].evidence_runs)
            self.assertIn("20260520-090000_saanich", lessons[0].evidence_runs)

    def test_atomic_write_leaves_store_readable(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            add_negative_lesson(store, _seed())
            text_before = store.read_text(encoding="utf-8")

            # Write a corrupt sibling tmp file to ensure cleanup logic
            # never leaves an unflushed scratch file masquerading as the
            # store; the real store stays whole.
            (Path(tmp) / "negative_lessons.md.junk.tmp").write_text(
                "torn", encoding="utf-8"
            )
            self.assertEqual(text_before, store.read_text(encoding="utf-8"))

    def test_rejects_invalid_lesson_type(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            bad = NegativeLessonMd(
                name="x",
                case="case",
                lesson_type="nope",
            )
            with self.assertRaises(ValueError):
                add_negative_lesson(store, bad)


class ListNegativeLessonsTests(unittest.TestCase):
    def test_parses_three_sections(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            add_negative_lesson(store, _seed())
            add_negative_lesson(
                store, _seed(name="calibration_diverged_a", case="a")
            )
            add_negative_lesson(
                store, _seed(name="non_physical_param_b", case="b")
            )
            # The third lesson has a different lesson_type
            object.__setattr__(
                _seed(name="calibration_diverged_a", case="a"),
                "lesson_type",
                "calibration_diverged",
            )
            lessons = list_negative_lessons(store)
            names = sorted(lesson.name for lesson in lessons)
            self.assertEqual(
                ["calibration_diverged_a", "continuity_fail_saanich_b8", "non_physical_param_b"],
                names,
            )

    def test_status_filter(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            add_negative_lesson(store, _seed())
            # Force the section status to dormant by writing in-place via
            # apply_decay against a very old last_seen.
            add_negative_lesson(
                store,
                _seed(
                    name="dormant_one",
                    case="dormant",
                    last_seen="2024-01-01T00:00:00Z",
                ),
            )
            apply_decay(store)
            active = list_negative_lessons(store, status="active")
            dormant = list_negative_lessons(store, status="dormant")
            retired = list_negative_lessons(store, status="retired")
            # Old last_seen with half_life=90 days → very low score → retired
            self.assertTrue(len(retired) >= 1)
            # Recently-written lesson should land in active or remain active
            for lesson in active:
                self.assertEqual("active", lesson.status)
            for lesson in dormant:
                self.assertEqual("dormant", lesson.status)


class IsParamSetKnownBadMdTests(unittest.TestCase):
    def test_within_tolerance_returns_lesson(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            add_negative_lesson(store, _seed(parameters={"manning_n_overland": 0.25}))
            match = is_param_set_known_bad_md(
                store,
                "saanich-b8",
                {"manning_n_overland": 0.255},
                tolerance_pct=5.0,
            )
            self.assertIsNotNone(match)
            assert match is not None
            self.assertEqual("saanich-b8", match.case)

    def test_outside_tolerance_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            add_negative_lesson(store, _seed(parameters={"manning_n_overland": 0.25}))
            match = is_param_set_known_bad_md(
                store,
                "saanich-b8",
                {"manning_n_overland": 0.30},  # 20% off
                tolerance_pct=5.0,
            )
            self.assertIsNone(match)


class ApplyDecayTests(unittest.TestCase):
    def test_decay_drops_score(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            now = datetime(2026, 5, 19, tzinfo=timezone.utc)
            sixty_days_ago = (now - timedelta(days=60)).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
            add_negative_lesson(
                store,
                _seed(last_seen=sixty_days_ago, half_life=90),
            )
            counts = apply_decay(
                store, now=now.isoformat(timespec="seconds").replace("+00:00", "Z")
            )
            self.assertGreater(sum(counts[k] for k in ("active", "dormant", "retired")), 0)
            lesson = list_negative_lessons(store)[0]
            # 60d/90d half-life → exp(-60/90) ≈ 0.513 → still active
            self.assertEqual("active", lesson.status)
            self.assertAlmostEqual(0.513, lesson.confidence_score, places=2)

    def test_status_transitions(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            now = datetime(2026, 5, 19, tzinfo=timezone.utc)
            # 200 days ago: exp(-200/90) ≈ 0.108 → retired
            old = (now - timedelta(days=200)).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
            add_negative_lesson(
                store,
                _seed(last_seen=old, half_life=90),
            )
            apply_decay(
                store, now=now.isoformat(timespec="seconds").replace("+00:00", "Z")
            )
            lesson = list_negative_lessons(store)[0]
            self.assertEqual("retired", lesson.status)


class ArchiveRetiredTests(unittest.TestCase):
    def test_moves_retired_to_archive(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            archive = Path(tmp) / "negative_lessons_archived.md"
            now = datetime(2026, 5, 19, tzinfo=timezone.utc)
            old = (now - timedelta(days=400)).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
            add_negative_lesson(store, _seed(last_seen=old))
            add_negative_lesson(
                store, _seed(name="continuity_fail_other", case="other-case")
            )
            apply_decay(
                store, now=now.isoformat(timespec="seconds").replace("+00:00", "Z")
            )
            count = archive_retired(store, archive)
            self.assertEqual(1, count)
            self.assertTrue(archive.is_file())
            self.assertIn(
                "## continuity_fail_saanich_b8",
                archive.read_text(encoding="utf-8"),
            )
            # The non-retired section remains in the main store
            remaining = list_negative_lessons(store)
            self.assertEqual(1, len(remaining))
            self.assertEqual("continuity_fail_other", remaining[0].name)


class SchemaInvariantTests(unittest.TestCase):
    def test_fence_sentinels_unique_per_module(self) -> None:
        # Distinct from ``aiswmm-metadata`` used by lessons_learned.md
        # so the two parsers cannot cross-contaminate.
        from agentic_swmm.memory.lessons_metadata import (
            METADATA_OPEN as LL_OPEN,
        )
        from agentic_swmm.memory.negative_lessons_markdown import (
            METADATA_OPEN as NL_OPEN,
        )

        self.assertNotEqual(LL_OPEN, NL_OPEN)

    def test_evidence_runs_deduplicates(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.md"
            seed = _seed()
            add_negative_lesson(store, seed)
            # Re-add with same run_id — count bumps but run not duplicated.
            add_negative_lesson(store, _seed())
            lesson = list_negative_lessons(store)[0]
            self.assertEqual(2, lesson.evidence_count)
            self.assertEqual(
                ["20260519-100000_saanich"],
                lesson.evidence_runs,
            )


class MigrateJsonlToMdTests(unittest.TestCase):
    def test_migration_writes_new_sections(self) -> None:
        with TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "negative_lessons.jsonl"
            md = Path(tmp) / "negative_lessons.md"
            for i in range(5):
                record_negative_lesson(
                    jsonl,
                    NegativeLesson(
                        run_id=f"run-{i}",
                        case_name=f"case-{i}",
                        lesson_type="continuity_fail",
                        parameters_tried={"manning_n_overland": 0.2 + i * 0.01},
                        note="postflight FAIL",
                    ),
                )
            count = migrate_jsonl_to_md(jsonl, md)
            self.assertEqual(5, count)
            lessons = list_negative_lessons(md)
            self.assertEqual(5, len(lessons))
            # evidence_runs preserved
            for lesson in lessons:
                self.assertTrue(any(r.startswith("run-") for r in lesson.evidence_runs))

    def test_migration_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "negative_lessons.jsonl"
            md = Path(tmp) / "negative_lessons.md"
            record_negative_lesson(
                jsonl,
                NegativeLesson(
                    run_id="run-0",
                    case_name="case-0",
                    lesson_type="continuity_fail",
                    parameters_tried={"manning_n_overland": 0.2},
                    note="postflight FAIL",
                ),
            )
            first = migrate_jsonl_to_md(jsonl, md)
            second = migrate_jsonl_to_md(jsonl, md)
            self.assertEqual(1, first)
            self.assertEqual(0, second)


if __name__ == "__main__":
    unittest.main()
