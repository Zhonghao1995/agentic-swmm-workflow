"""Status-transition policy (ME-2, issue #62).

``apply_decay`` recomputes ``confidence_score`` for every pattern in
``memory/modeling-memory/lessons_learned.md`` and then transitions
``status`` based on the new score:

- ``confidence_score >= 1.0`` → ``active``.
- ``0.2 <= confidence_score < 1.0`` → ``dormant``.
- ``confidence_score < 0.2`` → ``retired`` (moved to
  ``lessons_archived.md``).

Tests below seed a few fixture patterns with varying age / evidence
combinations and assert the resulting status. The fixtures are calibrated
for the default half-life of 90 days.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _seed_lessons(
    memory_dir: Path, *, fixtures: list[dict[str, object]]
) -> Path:
    """Write a lessons_learned.md file populated by the given fixtures.

    Each fixture is a dict with keys ``name``, ``last_seen_utc``,
    ``evidence_count``, ``status`` (initial), and ``half_life_days``.
    """
    lessons = memory_dir / "lessons_learned.md"
    body = ["<!-- schema_version: 1.1 -->\n", "# Lessons Learned\n\n"]
    for fixture in fixtures:
        body.append(f"## {fixture['name']}\n\n")
        body.append("<!-- aiswmm-metadata\n")
        body.append("metadata:\n")
        body.append(f"  first_seen_utc: {fixture['last_seen_utc']}\n")
        body.append(f"  last_seen_utc: {fixture['last_seen_utc']}\n")
        body.append(f"  evidence_count: {fixture['evidence_count']}\n")
        body.append("  evidence_runs: []\n")
        body.append(f"  status: {fixture['status']}\n")
        body.append(f"  confidence_score: {fixture['evidence_count']}.0\n")
        body.append(f"  half_life_days: {fixture['half_life_days']}\n")
        body.append("/aiswmm-metadata -->\n\n")
        body.append(f"Body text for {fixture['name']}.\n\n")
    lessons.write_text("".join(body), encoding="utf-8")
    return lessons


def _seed_archive(memory_dir: Path) -> Path:
    archive = memory_dir / "lessons_archived.md"
    archive.write_text(
        "<!-- schema_version: 1.1 -->\n# Lessons Archived\n\n",
        encoding="utf-8",
    )
    return archive


def test_recent_high_evidence_pattern_stays_active(tmp_path: Path) -> None:
    from agentic_swmm.memory.lessons_lifecycle import apply_decay
    from agentic_swmm.memory.lessons_metadata import read_all_patterns

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    now = datetime.now(timezone.utc)
    lessons = _seed_lessons(
        memory_dir,
        fixtures=[
            {
                "name": "recent_active_pattern",
                "last_seen_utc": _iso(now - timedelta(days=7)),
                "evidence_count": 5,
                "status": "active",
                "half_life_days": 90,
            }
        ],
    )
    archive = _seed_archive(memory_dir)

    report = apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    parsed = read_all_patterns(lessons.read_text(encoding="utf-8"))
    meta = parsed["recent_active_pattern"]
    assert meta is not None
    assert meta["status"] == "active"
    # Confidence ~ 5 * exp(-7/90) ~ 4.626.
    assert meta["confidence_score"] >= 1.0
    assert "recent_active_pattern" not in report.retired
    # No promotion either: it was already active.
    assert "recent_active_pattern" not in report.promoted


def test_mid_age_pattern_transitions_to_dormant(tmp_path: Path) -> None:
    from agentic_swmm.memory.lessons_lifecycle import apply_decay
    from agentic_swmm.memory.lessons_metadata import read_all_patterns

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    now = datetime.now(timezone.utc)
    lessons = _seed_lessons(
        memory_dir,
        fixtures=[
            {
                # 60 days, 2 evidence  -> confidence = 2 * exp(-60/90) ~ 1.027
                # but we want it dormant, so shift slightly.
                "name": "mid_age_dormant_pattern",
                "last_seen_utc": _iso(now - timedelta(days=120)),
                "evidence_count": 2,
                "status": "active",
                "half_life_days": 90,
            }
        ],
    )
    archive = _seed_archive(memory_dir)

    report = apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    parsed = read_all_patterns(lessons.read_text(encoding="utf-8"))
    meta = parsed["mid_age_dormant_pattern"]
    assert meta is not None
    assert meta["status"] == "dormant"
    assert 0.2 <= meta["confidence_score"] < 1.0
    assert "mid_age_dormant_pattern" in report.demoted


def test_old_low_evidence_pattern_is_retired(tmp_path: Path) -> None:
    from agentic_swmm.memory.lessons_lifecycle import apply_decay
    from agentic_swmm.memory.lessons_metadata import read_all_patterns

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    now = datetime.now(timezone.utc)
    lessons = _seed_lessons(
        memory_dir,
        fixtures=[
            {
                # 180 days, 1 evidence -> confidence = 1 * exp(-2) ~ 0.135
                "name": "old_retired_pattern",
                "last_seen_utc": _iso(now - timedelta(days=180)),
                "evidence_count": 1,
                "status": "active",
                "half_life_days": 90,
            }
        ],
    )
    archive = _seed_archive(memory_dir)

    report = apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    # Retired patterns are removed from lessons_learned.md and moved
    # into lessons_archived.md.
    parsed = read_all_patterns(lessons.read_text(encoding="utf-8"))
    assert "old_retired_pattern" not in parsed

    archive_text = archive.read_text(encoding="utf-8")
    assert "## old_retired_pattern" in archive_text
    # The archived block also carries the status=retired metadata.
    assert "status: retired" in archive_text

    assert "old_retired_pattern" in report.retired


def test_recent_seven_day_five_evidence_stays_active(tmp_path: Path) -> None:
    """Acceptance fixture: last_seen 7d ago, evidence=5 → active."""
    from agentic_swmm.memory.lessons_lifecycle import apply_decay
    from agentic_swmm.memory.lessons_metadata import read_all_patterns

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    now = datetime.now(timezone.utc)
    lessons = _seed_lessons(
        memory_dir,
        fixtures=[
            {
                "name": "seven_day_pattern",
                "last_seen_utc": _iso(now - timedelta(days=7)),
                "evidence_count": 5,
                "status": "active",
                "half_life_days": 90,
            }
        ],
    )
    archive = _seed_archive(memory_dir)

    apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    parsed = read_all_patterns(lessons.read_text(encoding="utf-8"))
    meta = parsed["seven_day_pattern"]
    assert meta is not None
    assert meta["status"] == "active"


def test_dormant_pattern_promotes_back_to_active_on_high_score(
    tmp_path: Path,
) -> None:
    from agentic_swmm.memory.lessons_lifecycle import apply_decay
    from agentic_swmm.memory.lessons_metadata import read_all_patterns

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    now = datetime.now(timezone.utc)
    lessons = _seed_lessons(
        memory_dir,
        fixtures=[
            {
                "name": "dormant_to_active",
                "last_seen_utc": _iso(now - timedelta(days=1)),
                "evidence_count": 4,
                "status": "dormant",
                "half_life_days": 90,
            }
        ],
    )
    archive = _seed_archive(memory_dir)

    report = apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    parsed = read_all_patterns(lessons.read_text(encoding="utf-8"))
    meta = parsed["dormant_to_active"]
    assert meta is not None
    assert meta["status"] == "active"
    assert "dormant_to_active" in report.promoted


def test_apply_decay_reports_unchanged_when_status_holds(
    tmp_path: Path,
) -> None:
    from agentic_swmm.memory.lessons_lifecycle import apply_decay
    from agentic_swmm.memory.lessons_metadata import read_all_patterns

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    now = datetime.now(timezone.utc)
    lessons = _seed_lessons(
        memory_dir,
        fixtures=[
            {
                "name": "still_active",
                "last_seen_utc": _iso(now - timedelta(days=1)),
                "evidence_count": 3,
                "status": "active",
                "half_life_days": 90,
            }
        ],
    )
    archive = _seed_archive(memory_dir)

    report = apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 1.0, "dormant_threshold": 0.2},
        now=now,
    )

    parsed = read_all_patterns(lessons.read_text(encoding="utf-8"))
    assert parsed["still_active"]["status"] == "active"
    assert "still_active" in report.unchanged


def test_apply_decay_reads_thresholds_from_config(tmp_path: Path) -> None:
    """Custom thresholds widen / narrow the dormant band."""
    from agentic_swmm.memory.lessons_lifecycle import apply_decay
    from agentic_swmm.memory.lessons_metadata import read_all_patterns

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    now = datetime.now(timezone.utc)
    lessons = _seed_lessons(
        memory_dir,
        fixtures=[
            {
                # Confidence ~ 5 * exp(-7/90) ~ 4.626. With active_threshold=10,
                # the same pattern should land in dormant.
                "name": "narrow_band",
                "last_seen_utc": _iso(now - timedelta(days=7)),
                "evidence_count": 5,
                "status": "active",
                "half_life_days": 90,
            }
        ],
    )
    archive = _seed_archive(memory_dir)

    apply_decay(
        lessons,
        archive,
        {"half_life_days": 90, "active_threshold": 10.0, "dormant_threshold": 0.2},
        now=now,
    )
    meta = read_all_patterns(lessons.read_text(encoding="utf-8"))["narrow_band"]
    assert meta is not None
    assert meta["status"] == "dormant"
