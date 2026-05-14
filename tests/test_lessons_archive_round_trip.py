"""Archive round-trip (ME-2, issue #62).

Verifies that retired patterns leave ``lessons_learned.md`` and end up
in ``lessons_archived.md`` with their full metadata fence intact, and
that the round-trip is reversible — moving the block back into
``lessons_learned.md`` and reapplying decay reads the metadata
correctly (so a future maintainer can revive a pattern by hand).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _write_lessons_with_two_patterns(memory_dir: Path, now: datetime) -> Path:
    lessons = memory_dir / "lessons_learned.md"
    old = now - timedelta(days=210)
    fresh = now - timedelta(days=2)
    lessons.write_text(
        "<!-- schema_version: 1.1 -->\n"
        "# Lessons Learned\n"
        "\n"
        "## stale_low_evidence\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        f"  first_seen_utc: {_iso(old)}\n"
        f"  last_seen_utc: {_iso(old)}\n"
        "  evidence_count: 1\n"
        "  evidence_runs:\n"
        "    - case-archive-1\n"
        "  status: active\n"
        "  confidence_score: 1.0\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "Body of the stale pattern, with two paragraphs of useful narrative.\n"
        "\n"
        "Recall via `recall_memory(\"stale_low_evidence\")`.\n"
        "\n"
        "## fresh_pattern\n"
        "\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        f"  first_seen_utc: {_iso(fresh)}\n"
        f"  last_seen_utc: {_iso(fresh)}\n"
        "  evidence_count: 4\n"
        "  evidence_runs: []\n"
        "  status: active\n"
        "  confidence_score: 4.0\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "Body of the fresh pattern.\n",
        encoding="utf-8",
    )
    return lessons


def test_retired_pattern_only_present_in_archive(tmp_path: Path) -> None:
    from agentic_swmm.memory.lessons_lifecycle import apply_decay

    now = datetime.now(timezone.utc)
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    lessons = _write_lessons_with_two_patterns(memory_dir, now)
    archive = memory_dir / "lessons_archived.md"

    report = apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    assert "stale_low_evidence" in report.retired
    lessons_text = lessons.read_text(encoding="utf-8")
    archive_text = archive.read_text(encoding="utf-8")
    # Stale pattern is in the archive, not the live file.
    assert "## stale_low_evidence" in archive_text
    assert "## stale_low_evidence" not in lessons_text
    # Fresh one stayed put.
    assert "## fresh_pattern" in lessons_text
    assert "## fresh_pattern" not in archive_text


def test_archived_block_keeps_metadata_fence(tmp_path: Path) -> None:
    from agentic_swmm.memory.lessons_lifecycle import apply_decay
    from agentic_swmm.memory.lessons_metadata import read_all_patterns

    now = datetime.now(timezone.utc)
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    lessons = _write_lessons_with_two_patterns(memory_dir, now)
    archive = memory_dir / "lessons_archived.md"

    apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    archive_text = archive.read_text(encoding="utf-8")
    parsed = read_all_patterns(archive_text)
    archived = parsed["stale_low_evidence"]
    assert archived is not None
    assert archived["status"] == "retired"
    # The body content survived.
    assert "useful narrative" in archive_text
    # The original evidence_runs survived too.
    assert "case-archive-1" in archived["evidence_runs"]


def test_archive_is_appended_to_not_overwritten(tmp_path: Path) -> None:
    """Multiple compact passes accumulate retired blocks.

    Retiring two different patterns over two ``apply_decay`` calls
    must leave both in the archive — the second pass must not clobber
    the first.
    """
    from agentic_swmm.memory.lessons_lifecycle import apply_decay

    now = datetime.now(timezone.utc)
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    lessons = _write_lessons_with_two_patterns(memory_dir, now)
    archive = memory_dir / "lessons_archived.md"

    apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    # Append a second stale block to lessons and re-run decay.
    old2 = now - timedelta(days=400)
    lessons.write_text(
        lessons.read_text(encoding="utf-8")
        + "\n## second_stale\n\n"
        "<!-- aiswmm-metadata\n"
        "metadata:\n"
        f"  first_seen_utc: {_iso(old2)}\n"
        f"  last_seen_utc: {_iso(old2)}\n"
        "  evidence_count: 1\n"
        "  evidence_runs: []\n"
        "  status: active\n"
        "  confidence_score: 1.0\n"
        "  half_life_days: 90\n"
        "/aiswmm-metadata -->\n"
        "\n"
        "Body of second stale pattern.\n",
        encoding="utf-8",
    )

    apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    archive_text = archive.read_text(encoding="utf-8")
    assert "## stale_low_evidence" in archive_text
    assert "## second_stale" in archive_text


def test_revived_pattern_round_trip(tmp_path: Path) -> None:
    """Moving an archived block back to lessons_learned restores it.

    Simulates a maintainer reviving a retired pattern: copy the block
    out of the archive, give it fresh evidence, and re-run decay. The
    pattern should land back in lessons_learned.md as active and the
    archive copy should remain (a manual cleanup step would handle the
    archive de-dup, but the live file is authoritative).
    """
    from agentic_swmm.memory.lessons_lifecycle import apply_decay
    from agentic_swmm.memory.lessons_metadata import (
        read_metadata,
        read_all_patterns,
        write_metadata,
        _iter_pattern_spans,
    )

    now = datetime.now(timezone.utc)
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    lessons = _write_lessons_with_two_patterns(memory_dir, now)
    archive = memory_dir / "lessons_archived.md"

    apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    archive_text = archive.read_text(encoding="utf-8")
    # Extract the retired block.
    revived_block = None
    for name, start, end in _iter_pattern_spans(archive_text):
        if name == "stale_low_evidence":
            revived_block = archive_text[start:end]
            break
    assert revived_block is not None

    # Bump metadata so the pattern survives a fresh decay pass.
    meta = read_metadata(revived_block)
    assert meta is not None
    meta["last_seen_utc"] = _iso(now)
    meta["evidence_count"] = 6
    meta["status"] = "active"
    revived_block = write_metadata(revived_block, meta)

    # Paste back into lessons_learned.md.
    lessons.write_text(
        lessons.read_text(encoding="utf-8") + "\n" + revived_block,
        encoding="utf-8",
    )

    # Re-run decay. The revived pattern should now sit in lessons as
    # active with confidence ~ 6.
    apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    parsed = read_all_patterns(lessons.read_text(encoding="utf-8"))
    revived_meta = parsed["stale_low_evidence"]
    assert revived_meta is not None
    assert revived_meta["status"] == "active"
    assert revived_meta["confidence_score"] >= 5.9


def test_apply_decay_creates_archive_header_when_missing(tmp_path: Path) -> None:
    from agentic_swmm.memory.lessons_lifecycle import apply_decay

    now = datetime.now(timezone.utc)
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    lessons = _write_lessons_with_two_patterns(memory_dir, now)
    archive = memory_dir / "lessons_archived.md"
    assert not archive.exists()

    apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    assert archive.exists()
    head = archive.read_text(encoding="utf-8").splitlines()[0:2]
    assert head[0].strip().startswith("<!--")
    # Title line is the second line.
    assert "Lessons Archived" in archive.read_text(encoding="utf-8")
