"""Backfill the cross-session SQLite store from existing ``runs/`` content.

Walk every ``runs/**/session_state.json`` we find, project it (and the
sibling ``agent_trace.jsonl``) into SQLite via the live sync projector,
and report counts.

The script is idempotent: re-running with ``--apply`` is a no-op
thanks to the unique indices on the messages and tool_events tables.
Use ``--rebuild`` to wipe the database before re-filling it from
scratch.

Usage::

    python scripts/backfill_sessions.py            # dry-run, prints counts
    python scripts/backfill_sessions.py --apply    # writes to runs/sessions.sqlite
    python scripts/backfill_sessions.py --apply --rebuild
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the package importable when this script runs from the repo
# root without an editable install. Mirrors what other scripts/ do.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentic_swmm.memory import session_db  # noqa: E402
from agentic_swmm.memory.session_sync import sync_session_to_db  # noqa: E402


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("runs"),
        help="Root folder to walk for session_state.json files. Default: runs/",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("runs/sessions.sqlite"),
        help="SQLite store to fill. Default: runs/sessions.sqlite",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to the database. Without this flag, runs a dry pass.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Delete the database before filling. Implies --apply.",
    )
    return parser


def discover_session_dirs(runs_root: Path) -> list[Path]:
    """Return every directory containing ``session_state.json`` under ``runs_root``."""
    if not runs_root.exists():
        return []
    found: set[Path] = set()
    for state in runs_root.rglob("session_state.json"):
        if state.is_file():
            found.add(state.parent)
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    runs_root: Path = args.runs_root.expanduser().resolve()
    db_path: Path = args.db_path.expanduser().resolve()

    if args.rebuild:
        if db_path.exists():
            db_path.unlink()
        args.apply = True

    session_dirs = discover_session_dirs(runs_root)
    print(f"discovered {len(session_dirs)} session dir(s) under {runs_root}")

    if not args.apply:
        # Dry-run: still report what we would have synced and what the
        # current store contains so the user can compare.
        existing = 0
        if db_path.exists():
            session_db.initialize(db_path)
            with session_db.connect(db_path) as conn:
                existing = len(session_db.list_session_ids(conn))
        print(
            f"DRY RUN: would write {len(session_dirs)} session(s) to {db_path}; "
            f"current store has {existing} session(s). Pass --apply to commit."
        )
        return 0

    session_db.initialize(db_path)
    total_sessions = 0
    total_messages = 0
    total_tool_events = 0
    failures: list[str] = []
    for session_dir in session_dirs:
        try:
            summary = sync_session_to_db(session_dir, db_path=db_path)
        except Exception as exc:  # pragma: no cover - defensive
            failures.append(f"{session_dir}: {exc}")
            continue
        if summary.get("ok"):
            total_sessions += 1
            total_messages += int(summary.get("messages", 0) or 0)
            total_tool_events += int(summary.get("tool_events", 0) or 0)
        else:
            failures.append(f"{session_dir}: {summary.get('reason', 'unknown')}")

    print(
        f"backfilled {total_sessions} sessions, "
        f"{total_messages} messages, {total_tool_events} tool_events"
    )
    if failures:
        print(f"skipped {len(failures)} session(s):")
        for line in failures[:20]:
            print(f"  - {line}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
