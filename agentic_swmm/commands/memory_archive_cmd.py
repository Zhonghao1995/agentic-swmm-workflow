"""``aiswmm memory archive`` and ``aiswmm memory restore`` — explicit entry management.

These are the materialized archive/restore verbs that move entries between
the live store and the archive sibling file.  Read-time tier filtering
(which happens without any explicit verb) is a separate code path in
``recall_search.py`` and ``context_budget.py``.

Usage::

    aiswmm memory archive <id>           # materialize archive for one entry
    aiswmm memory archive --auto         # materialize every currently archived-tier entry
    aiswmm memory restore <id>           # reverse a prior archive move

The ``archive --auto`` action does NOT silently run during recall — it is
an explicit user-triggered verb (Key invariant 4: modeling memory mutates
only via explicit verbs).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_swmm.utils.paths import resolve_memory_dir


def add_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``archive`` and ``restore`` under the ``memory`` subparser group."""

    # ── archive ────────────────────────────────────────────────────────────────
    archive_parser = sub.add_parser(
        "archive",
        help=(
            "Materialize the archive move for a memory entry that has resolved "
            "to the archived health tier.  Use --auto to move all such entries "
            "at once."
        ),
    )
    archive_parser.add_argument(
        "memory_id",
        nargs="?",
        default=None,
        help=(
            "Memory entry id (e.g. pm-abc123).  "
            "Required unless --auto is given."
        ),
    )
    archive_parser.add_argument(
        "--auto",
        action="store_true",
        help=(
            "Materialize every entry whose health tier currently resolves to "
            "'archived'.  Entries already moved are skipped."
        ),
    )
    archive_parser.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Modeling-memory directory. Defaults to memory/modeling-memory.",
    )
    archive_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="Emit machine-readable JSON result on stdout.",
    )
    archive_parser.set_defaults(func=archive_main)

    # ── restore ────────────────────────────────────────────────────────────────
    restore_parser = sub.add_parser(
        "restore",
        help=(
            "Reverse a prior archive move: append the entry back to the live "
            "store and mark the archive record as restored."
        ),
    )
    restore_parser.add_argument(
        "memory_id",
        help="Memory entry id to restore (e.g. pm-abc123).",
    )
    restore_parser.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Modeling-memory directory. Defaults to memory/modeling-memory.",
    )
    restore_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="Emit machine-readable JSON result on stdout.",
    )
    restore_parser.set_defaults(func=restore_main)


def archive_main(args: argparse.Namespace) -> int:
    """Entry point for ``aiswmm memory archive``."""
    from agentic_swmm.memory.memory_archive import archive_entry, auto_archive_all

    memory_dir = resolve_memory_dir(getattr(args, "memory_dir", None))
    json_out = getattr(args, "json_out", False)
    auto = getattr(args, "auto", False)
    memory_id = getattr(args, "memory_id", None)

    if auto:
        result = auto_archive_all(memory_dir)
        errors = result.get("errors", [])
        if json_out:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            archived = result.get("archived", [])
            if archived:
                print(f"Archived {len(archived)} entry(s):")
                for mid in archived:
                    print(f"  {mid}")
            else:
                print("No entries to archive (none currently in the archived tier).")
            if errors:
                print(f"\n{len(errors)} error(s):")
                for err in errors:
                    print(f"  {err}")
        return 1 if errors else 0

    if not memory_id:
        print(
            "error: either a memory_id argument or --auto is required.",
            end="\n",
        )
        return 1

    result = archive_entry(memory_id, memory_dir)
    if json_out:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        if result.get("ok"):
            print(f"Archived {memory_id} → {result.get('archive_path')}")
        else:
            print(f"error: {result.get('reason', 'unknown')}")
    return 0 if result.get("ok") else 1


def restore_main(args: argparse.Namespace) -> int:
    """Entry point for ``aiswmm memory restore``."""
    from agentic_swmm.memory.memory_archive import restore_entry

    memory_dir = resolve_memory_dir(getattr(args, "memory_dir", None))
    memory_id = args.memory_id
    json_out = getattr(args, "json_out", False)

    result = restore_entry(memory_id, memory_dir)
    if json_out:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        if result.get("ok"):
            print(f"Restored {memory_id} → {result.get('live_path')}")
        else:
            print(f"error: {result.get('reason', 'unknown')}")
    return 0 if result.get("ok") else 1
