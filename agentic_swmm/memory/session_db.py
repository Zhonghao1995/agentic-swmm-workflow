"""SQLite-backed cross-session memory store (PRD session-db-facts).

``agent_trace.jsonl`` remains the ground truth for any single session.
This module projects that JSONL into an FTS5-indexed SQLite database
so the agent can recall content from prior chat sessions ("we talked
about todcreek yesterday").

The schema is intentionally small (three tables + one FTS5 mirror)
and fully reconstructable from the underlying JSONL: deleting
``runs/sessions.sqlite`` and re-running the backfill script rebuilds
it. The database file itself is gitignored.

This module also owns the ``<previous-session>`` fence regex extension
applied to ``context_fence``. The fence is scrubbed from final
user-visible output so the planner cannot accidentally echo prior
sessions verbatim.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


SCHEMA_VERSION = "1.0"


_SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS schema_version (version TEXT PRIMARY KEY)",
    """
    CREATE TABLE IF NOT EXISTS sessions (
      session_id   TEXT PRIMARY KEY,
      start_utc    TEXT,
      end_utc      TEXT,
      goal         TEXT,
      case_name    TEXT,
      planner      TEXT,
      model        TEXT,
      ok           INTEGER,
      schema_version TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
      msg_id     INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT REFERENCES sessions(session_id),
      step       INTEGER,
      role       TEXT,
      text       TEXT,
      utc        TEXT
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
      text,
      content='messages',
      content_rowid='msg_id',
      tokenize='trigram'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
      INSERT INTO messages_fts(rowid, text) VALUES (new.msg_id, new.text);
    END
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_events (
      evt_id      INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id  TEXT REFERENCES sessions(session_id),
      step        INTEGER,
      kind        TEXT,
      tool_name   TEXT,
      args_json   TEXT,
      ok          INTEGER,
      summary     TEXT,
      stderr_tail TEXT,
      utc         TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_case ON sessions(case_name)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_end  ON sessions(end_utc)",
    "CREATE INDEX IF NOT EXISTS idx_tool_events_session ON tool_events(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)",
    # Unique composite makes tool_event ingestion idempotent under re-run.
    "CREATE UNIQUE INDEX IF NOT EXISTS uniq_tool_events_step ON tool_events(session_id, step, kind, tool_name)",
    # Unique composite makes message ingestion idempotent under re-run.
    "CREATE UNIQUE INDEX IF NOT EXISTS uniq_messages_step ON messages(session_id, step, role)",
)


_PREVIOUS_SESSION_FENCE = re.compile(
    r"<previous-session\b[^>]*>.*?</previous-session>",
    flags=re.DOTALL | re.IGNORECASE,
)


def initialize(db_path: Path) -> None:
    """Create the schema in ``db_path`` if it is not already present.

    Safe to call repeatedly — the IF NOT EXISTS clauses keep this
    idempotent. The schema_version row is upserted so the file always
    carries the current marker.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        for stmt in _SCHEMA_STATEMENTS:
            conn.execute(stmt)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version(version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Public context manager: yield an initialized connection."""
    initialize(db_path)
    with _connect(db_path) as conn:
        yield conn


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def upsert_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    start_utc: str | None,
    end_utc: str | None,
    goal: str | None,
    case_name: str | None,
    planner: str | None,
    model: str | None,
    ok: bool | None,
) -> None:
    """Insert or update the row for ``session_id`` in ``sessions``.

    ``end_utc`` and ``ok`` are usually written on session end, so the
    row may be inserted twice across a session's life (once on start
    with nulls, once on end with the final values). REPLACE is
    appropriate because the session_id is the canonical primary key.
    """
    conn.execute(
        """
        INSERT INTO sessions (
          session_id, start_utc, end_utc, goal, case_name,
          planner, model, ok, schema_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
          start_utc = COALESCE(excluded.start_utc, sessions.start_utc),
          end_utc   = COALESCE(excluded.end_utc, sessions.end_utc),
          goal      = COALESCE(excluded.goal, sessions.goal),
          case_name = COALESCE(excluded.case_name, sessions.case_name),
          planner   = COALESCE(excluded.planner, sessions.planner),
          model     = COALESCE(excluded.model, sessions.model),
          ok        = COALESCE(excluded.ok, sessions.ok),
          schema_version = excluded.schema_version
        """,
        (
            session_id,
            start_utc,
            end_utc,
            goal,
            case_name,
            planner,
            model,
            None if ok is None else int(bool(ok)),
            SCHEMA_VERSION,
        ),
    )


def insert_message(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    step: int,
    role: str,
    text: str,
    utc: str | None,
) -> None:
    """Insert one message row, deduped on ``(session_id, step, role)``.

    The composite unique index makes this safe to call on rerun. The
    AFTER INSERT trigger keeps ``messages_fts`` in sync automatically.
    """
    if not text:
        return
    try:
        conn.execute(
            "INSERT INTO messages (session_id, step, role, text, utc) VALUES (?, ?, ?, ?, ?)",
            (session_id, step, role, text, utc),
        )
    except sqlite3.IntegrityError:
        # Already inserted on a previous run: idempotent no-op.
        return


def insert_tool_event(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    step: int,
    kind: str,
    tool_name: str,
    args: dict[str, Any] | None,
    ok: bool | None,
    summary: str | None,
    stderr_tail: str | None,
    utc: str | None,
) -> None:
    """Insert a tool_call/tool_result row idempotently.

    Idempotency is keyed on ``(session_id, step, kind, tool_name)``; the
    unique index above silently swallows duplicates so re-running the
    backfill is a no-op.
    """
    args_json = json.dumps(args, sort_keys=True) if args is not None else None
    try:
        conn.execute(
            """
            INSERT INTO tool_events (
              session_id, step, kind, tool_name, args_json, ok, summary, stderr_tail, utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                step,
                kind,
                tool_name,
                args_json,
                None if ok is None else int(bool(ok)),
                summary,
                stderr_tail,
                utc,
            ),
        )
    except sqlite3.IntegrityError:
        return


def latest_session_for_case(
    conn: sqlite3.Connection, case_name: str
) -> dict[str, Any] | None:
    """Return the most recent ended session for ``case_name``, or ``None``.

    Used by the startup injector in ``runtime_loop`` to decide whether
    a ``<previous-session>`` banner should appear in the system prompt.
    Sessions with NULL ``end_utc`` are excluded so we never inject a
    still-in-progress session into a sibling.
    """
    if not case_name:
        return None
    row = conn.execute(
        """
        SELECT session_id, goal, ok, end_utc, case_name
        FROM sessions
        WHERE case_name = ? AND case_name IS NOT NULL AND end_utc IS NOT NULL
        ORDER BY end_utc DESC
        LIMIT 1
        """,
        (case_name,),
    ).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def search_messages(
    conn: sqlite3.Connection,
    query: str,
    *,
    case_name: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Search ``messages_fts`` and return rolled-up session-level hits.

    Each hit carries the matching session's metadata plus up to a few
    snippets of matched text. Optional ``case_name`` narrows the
    search; when ``case_name`` is ``None`` all sessions are eligible.
    """
    if not query.strip():
        return []
    safe_query = _sanitize_fts_query(query)
    if not safe_query:
        return []
    limit = max(1, min(int(limit or 5), 25))
    params: list[Any] = [safe_query]
    case_clause = ""
    if case_name:
        case_clause = " AND s.case_name = ?"
        params.append(case_name)
    rows = conn.execute(
        f"""
        SELECT m.session_id AS session_id,
               s.case_name  AS case_name,
               s.goal       AS goal,
               s.end_utc    AS end_utc,
               s.ok         AS ok,
               snippet(messages_fts, 0, '<<', '>>', '...', 12) AS snippet,
               m.step       AS step,
               m.role       AS role,
               bm25(messages_fts) AS score
        FROM messages_fts
        JOIN messages m ON m.msg_id = messages_fts.rowid
        JOIN sessions s ON s.session_id = m.session_id
        WHERE messages_fts MATCH ?
        {case_clause}
        ORDER BY score ASC
        LIMIT 200
        """,
        params,
    ).fetchall()

    rolled: dict[str, dict[str, Any]] = {}
    for row in rows:
        session_id = row["session_id"]
        bucket = rolled.setdefault(
            session_id,
            {
                "session_id": session_id,
                "case_name": row["case_name"],
                "goal": row["goal"],
                "end_utc": row["end_utc"],
                "ok": bool(row["ok"]) if row["ok"] is not None else None,
                "matched_snippets": [],
            },
        )
        if len(bucket["matched_snippets"]) < 3 and row["snippet"]:
            bucket["matched_snippets"].append(
                {
                    "step": row["step"],
                    "role": row["role"],
                    "text": row["snippet"],
                }
            )
        if len(rolled) >= limit and session_id not in rolled:
            break
    return list(rolled.values())[:limit]


def previous_session_block(session_row: dict[str, Any]) -> str:
    """Render the startup-injection banner for ``session_row``.

    Returns a small ``<previous-session>``-fenced string suitable for
    appending to the planner system prompt. The block is intentionally
    short (one or two lines) because it lives in the system prompt and
    every session pays for it in tokens.
    """
    case_name = session_row.get("case_name") or "unknown"
    session_id = session_row.get("session_id") or ""
    ok_value = session_row.get("ok")
    if ok_value is None:
        ok_attr = "unknown"
    else:
        ok_attr = "true" if bool(ok_value) else "false"
    ended = session_row.get("end_utc") or ""
    goal_text = (session_row.get("goal") or "").strip()
    if len(goal_text) > 240:
        goal_text = goal_text[:237].rstrip() + "..."
    lines = [
        f'<previous-session case="{case_name}" session_id="{session_id}" ok={ok_attr} ended="{ended}">',
        f"goal: {goal_text}" if goal_text else "goal: (none recorded)",
        "</previous-session>",
    ]
    return "\n".join(lines)


def scrub_previous_session(text: str) -> str:
    """Strip every ``<previous-session>...</previous-session>`` block."""
    if not text:
        return text
    return _PREVIOUS_SESSION_FENCE.sub("", text)


def previous_session_fence_pattern() -> re.Pattern[str]:
    """Expose the compiled regex so other scrubbers can chain it in."""
    return _PREVIOUS_SESSION_FENCE


def session_id_from_dir(session_dir: Path) -> str:
    """Derive a stable session_id from a session directory path.

    The convention is ``<date>_<HHMMSS_case_kind>``: the date directory
    is the parent (``YYYY-MM-DD``) and the leaf is the per-turn folder
    created by ``runtime_loop._new_turn_dir``. The id is reversible from
    the directory layout so backfill can reconstruct it without
    additional state.
    """
    leaf = session_dir.name
    parent = session_dir.parent.name
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", parent):
        return f"{parent.replace('-', '')}_{leaf}"
    return leaf


def utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_session_ids(conn: sqlite3.Connection) -> list[str]:
    """Return every session_id currently in the store (test helper)."""
    rows = conn.execute("SELECT session_id FROM sessions ORDER BY session_id").fetchall()
    return [row["session_id"] for row in rows]


def session_messages(
    conn: sqlite3.Connection, session_id: str
) -> list[dict[str, Any]]:
    """Return raw messages for ``session_id`` (test helper)."""
    rows = conn.execute(
        "SELECT step, role, text, utc FROM messages WHERE session_id = ? ORDER BY step, msg_id",
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def session_tool_events(
    conn: sqlite3.Connection, session_id: str
) -> list[dict[str, Any]]:
    """Return tool events for ``session_id`` (test helper)."""
    rows = conn.execute(
        "SELECT step, kind, tool_name, ok, summary FROM tool_events "
        "WHERE session_id = ? ORDER BY step, evt_id",
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _sanitize_fts_query(query: str) -> str:
    """Make ``query`` safe for an FTS5 MATCH expression.

    FTS5 treats characters like ``"`` and ``-`` as operators. We strip
    those and quote each remaining token so the planner can feed in
    free-form text (including ``--auto-window-mode`` style arguments)
    without crashing the query parser.
    """
    cleaned = re.sub(r"[\"\\\\]+", " ", query.strip())
    tokens = [token for token in re.split(r"\s+", cleaned) if token]
    if not tokens:
        return ""
    return " ".join(f'"{token}"' for token in tokens)


def chunked_messages_from_events(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project trace events into ``(step, role, text, utc)`` message rows.

    Centralised here so the live end-of-session sync path and the
    backfill script produce identical rows. The planner trace uses a
    handful of event names; we recognise the user/assistant text
    surfaces and skip everything else.
    """
    rows: list[dict[str, Any]] = []
    user_step = 0
    assistant_step = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("event")
        utc = event.get("timestamp_utc")
        if kind == "session_start":
            goal = event.get("goal")
            if goal:
                user_step += 1
                rows.append(
                    {
                        "step": user_step,
                        "role": "user",
                        "text": str(goal),
                        "utc": utc,
                    }
                )
        elif kind == "user_prompt":
            text = event.get("text") or event.get("prompt")
            if text:
                user_step += 1
                rows.append(
                    {
                        "step": user_step,
                        "role": "user",
                        "text": str(text),
                        "utc": utc,
                    }
                )
        elif kind == "planner_response":
            text = event.get("text")
            if text:
                assistant_step += 1
                rows.append(
                    {
                        "step": assistant_step,
                        "role": "assistant",
                        "text": str(text),
                        "utc": utc,
                    }
                )
        elif kind == "assistant_text":
            text = event.get("text") or event.get("final_text")
            if text:
                assistant_step += 1
                rows.append(
                    {
                        "step": assistant_step,
                        "role": "assistant",
                        "text": str(text),
                        "utc": utc,
                    }
                )
        elif kind == "session_end":
            text = event.get("final_text")
            if text:
                assistant_step += 1
                rows.append(
                    {
                        "step": assistant_step,
                        "role": "assistant",
                        "text": str(text),
                        "utc": utc,
                    }
                )
    return rows


def tool_events_from_trace(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project trace events into tool_call / tool_result rows.

    The planner_response event carries the structured tool_calls list,
    and tool execution emits separate tool_result-bearing records.
    """
    rows: list[dict[str, Any]] = []
    step = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("event")
        utc = event.get("timestamp_utc")
        if kind == "planner_response":
            for call in event.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                step += 1
                rows.append(
                    {
                        "step": step,
                        "kind": "tool_call",
                        "tool_name": str(call.get("tool") or call.get("name") or ""),
                        "args": call.get("args") or call.get("arguments") or {},
                        "ok": None,
                        "summary": None,
                        "stderr_tail": None,
                        "utc": utc,
                    }
                )
        elif kind == "tool_call":
            step += 1
            rows.append(
                {
                    "step": step,
                    "kind": "tool_call",
                    "tool_name": str(event.get("tool") or ""),
                    "args": event.get("args") or {},
                    "ok": None,
                    "summary": None,
                    "stderr_tail": None,
                    "utc": utc,
                }
            )
        elif kind == "tool_result":
            step += 1
            rows.append(
                {
                    "step": step,
                    "kind": "tool_result",
                    "tool_name": str(event.get("tool") or ""),
                    "args": event.get("args") or {},
                    "ok": event.get("ok"),
                    "summary": event.get("summary"),
                    "stderr_tail": event.get("stderr_tail"),
                    "utc": utc,
                }
            )
    return rows
