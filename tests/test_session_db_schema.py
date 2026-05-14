"""Schema + FTS5 contract for the cross-session SQLite store.

Covers PRD A "Conversation history" layer: three tables, FTS5 mirror,
trigger, indices, and trigram tokenisation good enough for Chinese
queries against the message text.
"""

from __future__ import annotations

from pathlib import Path


def _table_names(conn) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ).fetchall()
    return {row["name"] for row in rows}


def _trigger_names(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
    return {row["name"] for row in rows}


def _index_names(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {row["name"] for row in rows}


def test_initialize_creates_three_tables_plus_fts(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db

    db_path = tmp_path / "sessions.sqlite"
    session_db.initialize(db_path)
    assert db_path.exists()

    with session_db.connect(db_path) as conn:
        tables = _table_names(conn)
        assert "schema_version" in tables
        assert "sessions" in tables
        assert "messages" in tables
        assert "tool_events" in tables
        assert "messages_fts" in tables

        triggers = _trigger_names(conn)
        assert "messages_ai" in triggers

        indices = _index_names(conn)
        for expected in (
            "idx_sessions_case",
            "idx_sessions_end",
            "idx_tool_events_session",
            "idx_messages_session",
        ):
            assert expected in indices

        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row is not None
        assert row["version"] == session_db.SCHEMA_VERSION


def test_fts5_trigram_finds_chinese_query_text(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db

    db_path = tmp_path / "sessions.sqlite"
    session_db.initialize(db_path)
    with session_db.connect(db_path) as conn:
        session_db.upsert_session(
            conn,
            session_id="20260513_223040_todcreek_run",
            start_utc="2026-05-13T22:30:40+00:00",
            end_utc="2026-05-13T22:31:00+00:00",
            goal="todcreek 洪峰流量分析",
            case_name="todcreek",
            planner="openai",
            model="gpt-5.5",
            ok=True,
        )
        session_db.insert_message(
            conn,
            session_id="20260513_223040_todcreek_run",
            step=1,
            role="user",
            text="请帮我画一张洪峰流量的图",
            utc="2026-05-13T22:30:41+00:00",
        )
        session_db.insert_message(
            conn,
            session_id="20260513_223040_todcreek_run",
            step=1,
            role="assistant",
            text="我会调用 plot_run 生成图像",
            utc="2026-05-13T22:30:42+00:00",
        )
        conn.commit()

        hits = session_db.search_messages(conn, "洪峰流量")
        assert hits, "trigram tokenizer must find Chinese substring 洪峰流量"
        assert hits[0]["session_id"] == "20260513_223040_todcreek_run"
        assert hits[0]["case_name"] == "todcreek"
        assert hits[0]["matched_snippets"], "expected at least one snippet"


def test_insert_message_dedups_on_session_step_role(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db

    db_path = tmp_path / "sessions.sqlite"
    session_db.initialize(db_path)
    with session_db.connect(db_path) as conn:
        session_db.upsert_session(
            conn,
            session_id="s1",
            start_utc="2026-05-13T00:00:00+00:00",
            end_utc="2026-05-13T00:00:10+00:00",
            goal="g",
            case_name=None,
            planner=None,
            model=None,
            ok=True,
        )
        for _ in range(3):
            session_db.insert_message(
                conn,
                session_id="s1",
                step=1,
                role="user",
                text="hello",
                utc="2026-05-13T00:00:01+00:00",
            )
        conn.commit()
        rows = session_db.session_messages(conn, "s1")
        assert len(rows) == 1


def test_insert_tool_event_dedups_on_composite_key(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db

    db_path = tmp_path / "sessions.sqlite"
    session_db.initialize(db_path)
    with session_db.connect(db_path) as conn:
        session_db.upsert_session(
            conn,
            session_id="s1",
            start_utc="t",
            end_utc="t2",
            goal=None,
            case_name=None,
            planner=None,
            model=None,
            ok=True,
        )
        for _ in range(2):
            session_db.insert_tool_event(
                conn,
                session_id="s1",
                step=3,
                kind="tool_call",
                tool_name="plot_run",
                args={"node": "O1"},
                ok=None,
                summary=None,
                stderr_tail=None,
                utc="t",
            )
        conn.commit()
        rows = session_db.session_tool_events(conn, "s1")
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "plot_run"


def test_latest_session_for_case_skips_open_sessions(tmp_path: Path) -> None:
    from agentic_swmm.memory import session_db

    db_path = tmp_path / "sessions.sqlite"
    session_db.initialize(db_path)
    with session_db.connect(db_path) as conn:
        session_db.upsert_session(
            conn,
            session_id="open",
            start_utc="2026-05-13T00:00:00+00:00",
            end_utc=None,
            goal="ongoing",
            case_name="tecnopolo",
            planner=None,
            model=None,
            ok=None,
        )
        session_db.upsert_session(
            conn,
            session_id="closed",
            start_utc="2026-05-12T00:00:00+00:00",
            end_utc="2026-05-12T00:01:00+00:00",
            goal="finished",
            case_name="tecnopolo",
            planner=None,
            model=None,
            ok=False,
        )
        conn.commit()
        row = session_db.latest_session_for_case(conn, "tecnopolo")
        assert row is not None
        assert row["session_id"] == "closed"
