"""A long natural query still finds the day's sessions (F-65).

Live finding 2026-09-02 (scenario S17 turn 3): `recall_session_history`
matched 0 sessions for "downtown Victoria run earlier today compare run
directories, current run and previous run" although twenty downtown
Victoria sessions had run that day. FTS5 joins bare terms with AND.
"""

from __future__ import annotations

from agentic_swmm.memory import session_db


def _seed(db_path):
    session_db.initialize(db_path)
    with session_db.connect(db_path) as conn:
        for i, (sid, goal) in enumerate(
            [
                ("session-172310", "Fetch a SWMM model from the Canada service for downtown Victoria BC and run it"),
                ("session-171755", "Run it again but this time do not fetch anything new"),
            ]
        ):
            session_db.upsert_session(
                conn, session_id=sid, start_utc="2026-09-02T17:00:00", end_utc="2026-09-02T17:05:00",
                goal=goal, case_name="downtown-victoria-bc", planner="llm", model="test", ok=True,
            )
            session_db.insert_message(conn, session_id=sid, step=1, role="user", text=goal, utc="2026-09-02T17:00:00")
            session_db.insert_message(
                conn, session_id=sid, step=2, role="assistant",
                text="Peak inflow 0.116 m3/s at OUT_DMH002395; audit Pass; run directory runs/2026-09-02/" + sid,
                utc="2026-09-02T17:05:00",
            )
        conn.commit()


def test_short_queries_stay_precise():
    assert session_db._sanitize_fts_query("downtown Victoria") == '"downtown" "Victoria"'


def test_tokens_too_short_for_the_trigram_index_are_dropped():
    assert session_db._sanitize_fts_query("run it in BC") == '"run"'


def test_long_queries_match_any_word():
    fts = session_db._sanitize_fts_query("downtown Victoria run earlier today compare run directories")
    assert " OR " in fts
    assert fts.startswith('"downtown" OR "Victoria"')


def test_the_live_query_now_finds_the_days_sessions(tmp_path):
    db = tmp_path / "sessions.sqlite"
    _seed(db)
    with session_db.connect(db) as conn:
        hits = session_db.search_messages(
            conn,
            "downtown Victoria run earlier today compare run directories, current run and previous run",
            case_name="downtown-victoria-bc",
            limit=10,
        )
    assert hits, "the natural query must match"
    assert {h["session_id"] for h in hits} >= {"session-172310"}


def test_a_two_word_query_still_requires_both_words(tmp_path):
    db = tmp_path / "sessions.sqlite"
    _seed(db)
    with session_db.connect(db) as conn:
        assert session_db.search_messages(conn, "downtown Saskatoon", limit=5) == []
