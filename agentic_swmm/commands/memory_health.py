"""``aiswmm memory health`` — application outcome log viewer (Phase 1).

Read-only debug verb.  Shows the derived health score and event history
for one memory entry (or the top-N lowest-health entries when no id is
given).

Usage::

    aiswmm memory health <memory-id>      # score + event table for one entry
    aiswmm memory health                  # top-10 lowest-health entries
    aiswmm memory health --top 20         # show up to 20 entries
    aiswmm memory health --memory-dir <path>
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from agentic_swmm.utils.paths import resolve_memory_dir


def add_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``health`` under the ``memory`` subparser group."""
    parser = sub.add_parser(
        "health",
        help=(
            "Show the memory health score and application outcome log for "
            "a memory entry.  With no memory-id, lists the lowest-health "
            "entries."
        ),
    )
    parser.add_argument(
        "memory_id",
        nargs="?",
        default=None,
        help=(
            "Memory entry id (e.g. pm-abc123, cm-def456).  "
            "Omit to list the lowest-health entries across all ids."
        ),
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        metavar="N",
        help="Number of entries to show when no memory-id is given (default: 10).",
    )
    parser.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Modeling-memory directory. Defaults to memory/modeling-memory.",
    )
    parser.set_defaults(func=health_main)


def _fmt_score(score: float) -> str:
    """Format health score as a percentage bar."""
    bar_len = 20
    filled = round(score * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    return f"{score:.3f}  [{bar}]"


def _print_single_entry(memory_id: str, events: list[dict], score: float) -> None:
    """Print score + event history table for one memory entry."""
    print(f"\nMemory health — {memory_id}")
    print("=" * 60)
    print(f"  health score : {_fmt_score(score)}")
    print(f"  event count  : {len(events)}")
    print()

    if not events:
        print("  No outcome events recorded yet.")
        return

    # Table header
    print(
        f"  {'#':>3}  {'event':15}  {'attribution':12}  {'metric_val':>10}  ts_utc"
    )
    print("  " + "-" * 70)

    for i, ev in enumerate(events, 1):
        ev_type = str(ev.get("event", "?"))
        attr = str(ev.get("attribution", "?"))
        ts = str(ev.get("ts_utc", "?"))
        m = ev.get("metric") or {}
        mv = m.get("value")
        mv_str = f"{mv:.4f}" if mv is not None else "—"
        print(f"  {i:>3}  {ev_type:15}  {attr:12}  {mv_str:>10}  {ts}")

    print()


def _print_summary_table(summaries: list[dict[str, Any]]) -> None:
    """Print lowest-health summary table."""
    print("\nMemory health — lowest entries")
    print("=" * 60)
    if not summaries:
        print("  No outcome events recorded yet.")
        return

    print(f"  {'memory_id':30}  {'score':>7}  {'events':>7}")
    print("  " + "-" * 55)
    for row in summaries:
        mid = str(row.get("memory_id", "?"))
        score = float(row.get("health_score", 0))
        n = int(row.get("event_count", 0))
        print(f"  {mid:30}  {score:7.3f}  {n:7d}")
    print()


def health_main(args: argparse.Namespace) -> int:
    """Entry point for ``aiswmm memory health``."""
    from agentic_swmm.memory.memory_outcomes import (
        OUTCOME_LEDGER_FILENAME,
        events_for_memory,
        health_score,
        load_outcome_events,
        summary_for_all,
    )

    memory_dir = resolve_memory_dir(getattr(args, "memory_dir", None))
    store_path = memory_dir / OUTCOME_LEDGER_FILENAME

    all_events = load_outcome_events(store_path)

    memory_id = getattr(args, "memory_id", None)

    if memory_id:
        ev_for_id = events_for_memory(all_events, memory_id)
        score = health_score(memory_id, ev_for_id)
        _print_single_entry(memory_id, ev_for_id, score)
    else:
        top_n = max(1, int(getattr(args, "top", 10)))
        summaries = summary_for_all(all_events, top_n=top_n, lowest_first=True)
        _print_summary_table(summaries)

    return 0
