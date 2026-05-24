"""Integrity-check helper for the cross-session SQLite store (issue #204).

The helper runs ``PRAGMA integrity_check`` against ``sessions.sqlite``
and surfaces corruption as a structured dataclass so doctor can render
it and the repair verb can decide whether to rebuild. Three cases are
covered:

* Absent file -> ``IntegrityReport(state="absent")`` (will be created
  by the first session sync; no action required).
* Healthy file -> ``IntegrityReport(state="ok")`` with row counts plus
  the file size on disk.
* Corrupt file -> ``IntegrityReport(state="corrupt")`` with a non-empty
  ``errors`` list so callers can show how many rows the DB reported.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


def _seed_intact_db(db_path: Path) -> None:
    """Create a minimal, syntactically valid sessions.sqlite.

    The DB is seeded with enough rows + chunky message text that the
    schema is sure to span more than one page on disk — that lets the
    corruption helper smash content pages while leaving the header
    intact.
    """
    from agentic_swmm.memory import session_db

    session_db.initialize(db_path)
    with session_db.connect(db_path) as conn:
        session_db.upsert_session(
            conn,
            session_id="20260524_120000_demo_run",
            start_utc="2026-05-24T12:00:00+00:00",
            end_utc="2026-05-24T12:00:30+00:00",
            goal="demo",
            case_name="demo",
            planner="openai",
            model="gpt-5",
            ok=True,
        )
        for step in range(1, 16):
            session_db.insert_message(
                conn,
                session_id="20260524_120000_demo_run",
                step=step,
                role="user" if step % 2 else "assistant",
                # Multi-page-worth of text so the integrity check has
                # plenty of material to verify against.
                text=f"message {step} " * 80,
                utc="2026-05-24T12:00:01+00:00",
            )
        conn.commit()


def _corrupt_db_in_place(db_path: Path) -> None:
    """Smash bytes in the middle of a real SQLite file so PRAGMA
    integrity_check reports failures.

    The header (offset 0..99) and the tail (last 8 bytes) are left
    intact so ``sqlite3.connect`` still opens the connection — the
    corruption lives in the content pages where ``PRAGMA
    integrity_check`` reads the schema.
    """
    raw = bytearray(db_path.read_bytes())
    # Stomp every byte from offset 200 onwards (skipping the last 8) so
    # multiple pages are guaranteed broken.
    for i in range(200, len(raw) - 8):
        raw[i] = 0xAA
    db_path.write_bytes(bytes(raw))


def test_integrity_check_reports_absent_when_file_missing(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db

    db_path = tmp_path / "sessions.sqlite"
    assert not db_path.exists()

    report = session_db.integrity_check(db_path)

    assert report.state == "absent"
    assert report.errors == ()
    assert report.path == db_path
    assert report.session_count is None
    assert report.message_count is None
    assert report.size_bytes is None


def test_integrity_check_reports_ok_for_intact_db(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db

    db_path = tmp_path / "sessions.sqlite"
    _seed_intact_db(db_path)

    report = session_db.integrity_check(db_path)

    assert report.state == "ok"
    assert report.errors == ()
    assert report.session_count == 1
    assert report.message_count == 15
    assert report.size_bytes is not None and report.size_bytes > 0


def test_integrity_check_reports_corrupt_for_smashed_db(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db

    db_path = tmp_path / "sessions.sqlite"
    _seed_intact_db(db_path)
    _corrupt_db_in_place(db_path)

    report = session_db.integrity_check(db_path)

    assert report.state == "corrupt"
    # PRAGMA integrity_check returns one row per failure; we expect at
    # least one (often dozens) for a smashed page.
    assert len(report.errors) >= 1
    # The errors list is plain-string diagnostics so doctor/repair can
    # surface them without further parsing.
    assert all(isinstance(err, str) for err in report.errors)


def test_integrity_check_is_cached_per_path_per_process(tmp_path: Path) -> None:
    """The integrity check is called eagerly on every connection open;
    cache the verdict per-path so we don't pay the PRAGMA round-trip on
    every session-end sync.
    """
    from agentic_swmm.memory import session_db

    db_path = tmp_path / "sessions.sqlite"
    _seed_intact_db(db_path)

    # Clear any prior cache state so the test is hermetic.
    session_db.clear_integrity_cache()

    first = session_db.integrity_check(db_path)
    second = session_db.integrity_check(db_path)

    # Same object identity == cache hit.
    assert first is second
    assert first.state == "ok"
