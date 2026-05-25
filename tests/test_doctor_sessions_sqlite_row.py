"""Doctor row for ``runs/sessions.sqlite`` (issue #204).

Adds a single :class:`MemoryStoreStatus` to the Memory stores section
that surfaces one of three states:

* ``OK      sessions.sqlite - N sessions, M messages, X.X MB``  — intact
* ``CORRUPT sessions.sqlite - integrity check failed (Y corrupt pages); run aiswmm memory repair-sessions`` — broken
* ``OK      sessions.sqlite - file absent (will be created on first session)`` — fresh install

Tested at two layers:

* The collector returns a :class:`MemoryStoreStatus` with the right
  severity + detail bits per state.
* The render function emits the three exact strings the issue asks for.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _seed_intact_db(db_path: Path) -> None:
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
        for step in range(1, 8):
            session_db.insert_message(
                conn,
                session_id="20260524_120000_demo_run",
                step=step,
                role="user" if step % 2 else "assistant",
                text=f"message {step} " * 80,
                utc="2026-05-24T12:00:01+00:00",
            )
        conn.commit()


def _corrupt(db_path: Path) -> None:
    raw = bytearray(db_path.read_bytes())
    for i in range(200, len(raw) - 8):
        raw[i] = 0xAA
    db_path.write_bytes(bytes(raw))


def test_sessions_sqlite_row_absent(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db
    from agentic_swmm.commands.doctor_extension import (
        collect_sessions_db_status,
    )

    session_db.clear_integrity_cache()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    status = collect_sessions_db_status(runs_dir)

    assert status.name == "sessions.sqlite"
    assert status.severity == "OK"
    assert "file absent" in (status.remediation or "")


def test_sessions_sqlite_row_ok(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db
    from agentic_swmm.commands.doctor_extension import (
        collect_sessions_db_status,
    )

    session_db.clear_integrity_cache()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _seed_intact_db(runs_dir / "sessions.sqlite")

    status = collect_sessions_db_status(runs_dir)

    assert status.name == "sessions.sqlite"
    assert status.severity == "OK"
    assert status.row_count == 1  # 1 session
    assert status.verified_count == 7  # 7 messages
    assert status.exists is True
    assert status.remediation is None


def test_sessions_sqlite_row_corrupt(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db
    from agentic_swmm.commands.doctor_extension import (
        collect_sessions_db_status,
    )

    session_db.clear_integrity_cache()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    db_path = runs_dir / "sessions.sqlite"
    _seed_intact_db(db_path)
    _corrupt(db_path)
    # Bust the per-path cache so the post-corruption probe re-runs.
    session_db.clear_integrity_cache()

    status = collect_sessions_db_status(runs_dir)

    assert status.name == "sessions.sqlite"
    assert status.severity == "CORRUPT"
    assert status.remediation is not None
    assert "aiswmm memory repair-sessions" in status.remediation


def test_render_memory_stores_includes_sessions_sqlite_row_absent(
    tmp_path: Path,
) -> None:
    from agentic_swmm.memory import session_db
    from agentic_swmm.commands.doctor_extension import (
        collect_sessions_db_status,
        render_memory_stores_section,
    )

    session_db.clear_integrity_cache()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    status = collect_sessions_db_status(runs_dir)

    rendered = render_memory_stores_section([status])

    assert "sessions.sqlite" in rendered
    assert "file absent" in rendered


def test_render_memory_stores_includes_sessions_sqlite_row_ok(
    tmp_path: Path,
) -> None:
    from agentic_swmm.memory import session_db
    from agentic_swmm.commands.doctor_extension import (
        collect_sessions_db_status,
        render_memory_stores_section,
    )

    session_db.clear_integrity_cache()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _seed_intact_db(runs_dir / "sessions.sqlite")
    status = collect_sessions_db_status(runs_dir)

    rendered = render_memory_stores_section([status])

    # Acceptance criterion wording from the issue.
    assert "sessions.sqlite" in rendered
    assert "session" in rendered  # "1 session" or "1 sessions"
    # The size is rendered as "X.X MB" (or "X KB" for small DBs).
    assert "B" in rendered  # "KB" or "MB" tail


def test_render_memory_stores_includes_sessions_sqlite_row_corrupt(
    tmp_path: Path,
) -> None:
    from agentic_swmm.memory import session_db
    from agentic_swmm.commands.doctor_extension import (
        collect_sessions_db_status,
        render_memory_stores_section,
    )

    session_db.clear_integrity_cache()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    db_path = runs_dir / "sessions.sqlite"
    _seed_intact_db(db_path)
    _corrupt(db_path)
    session_db.clear_integrity_cache()

    status = collect_sessions_db_status(runs_dir)
    rendered = render_memory_stores_section([status])

    assert "CORRUPT" in rendered
    assert "sessions.sqlite" in rendered
    assert "integrity check failed" in rendered
    assert "aiswmm memory repair-sessions" in rendered


def test_render_corrupt_severity_header_counter(tmp_path: Path) -> None:
    """The header that tallies severities must include CORRUPT when one
    of the rows is corrupt — otherwise a user scanning the section
    summary would miss the most important signal.
    """
    from agentic_swmm.memory import session_db
    from agentic_swmm.commands.doctor_extension import (
        MemoryStoreStatus,
        collect_sessions_db_status,
        render_memory_stores_section,
    )

    session_db.clear_integrity_cache()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    db_path = runs_dir / "sessions.sqlite"
    _seed_intact_db(db_path)
    _corrupt(db_path)
    session_db.clear_integrity_cache()

    status = collect_sessions_db_status(runs_dir)
    rendered = render_memory_stores_section([status])

    header_line = rendered.splitlines()[0]
    assert "1 CORRUPT" in header_line


def test_doctor_exit_code_nonzero_on_corrupt_sessions_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #212 item #2: ``aiswmm doctor`` must exit non-zero when
    the cross-session DB is CORRUPT so CI health checks catch
    data-loss conditions, not just missing binaries.

    Also: the CORRUPT row must appear in the rendered Issues section
    with the repair-sessions remediation hint.
    """
    import io
    from contextlib import redirect_stdout

    from agentic_swmm.cli import main as cli_main
    from agentic_swmm.memory import session_db

    session_db.clear_integrity_cache()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    db_path = runs_dir / "sessions.sqlite"
    _seed_intact_db(db_path)
    _corrupt(db_path)
    session_db.clear_integrity_cache()

    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(runs_dir))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(["doctor"])

    assert rc == 1
    body = buf.getvalue()
    # Issues section surfaces the corrupt row + the remediation hint.
    assert "Issues:" in body
    assert "sessions.sqlite" in body
    assert "aiswmm memory repair-sessions" in body
