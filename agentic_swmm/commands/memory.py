from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.utils.paths import resolve_memory_dir, resolve_runs_dir


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "memory",
        help="What aiswmm remembers: show a case, review proposals, rebuild the database, health and archive.",
    )
    register_example_flag(parser, example_text="aiswmm memory show <case>")
    parser.set_defaults(func=_dispatch)

    sub = parser.add_subparsers(dest="memory_command")
    show = sub.add_parser(
        "show",
        help="Print a plain-text memory card for one case (what aiswmm remembers about it).",
    )
    show.add_argument(
        "case",
        type=str,
        help="Case id / slug to show the memory card for (the slug you ran with --case-id).",
    )
    show.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Memory store directory. Defaults to memory/store.",
    )
    show.set_defaults(func=show_main)

    # Memory simplification PR 4: proposals await a human decision.
    proposals = sub.add_parser(
        "proposals",
        help="List the proposals awaiting a decision (a fix seen often enough, or a fact the agent proposed).",
    )
    proposals.add_argument("--all", action="store_true", help="Include promoted and rejected proposals.")
    proposals.add_argument("--json", action="store_true", help="Emit the list as JSON.")
    proposals.set_defaults(func=proposals_main)

    promote = sub.add_parser(
        "promote",
        help="Apply one proposal to its target file (facts.md anywhere; a SKILL.md or the initial memory in a source checkout).",
    )
    promote.add_argument("proposal_id", type=str, help="The proposal id (e.g. 003).")
    promote.set_defaults(func=promote_main)

    reject = sub.add_parser(
        "reject",
        help="Decline one proposal; the same proposal is never made again.",
    )
    reject.add_argument("proposal_id", type=str, help="The proposal id (e.g. 003).")
    reject.add_argument("--reason", type=str, default="", help="Why, recorded in the proposal file.")
    reject.set_defaults(func=reject_main)

    rebuild = sub.add_parser(
        "rebuild",
        help=(
            "Set the memory database aside and rebuild it from the ledgers in "
            "the store and the run records under runs/ (sessions, runs, "
            "failures, parametric, calibration, negative lessons). Deleting the "
            "database never loses anything; this is how it comes back."
        ),
    )
    rebuild.add_argument(
        "--runs-dir",
        dest="rebuild_runs_dir",
        type=Path,
        default=None,
        help="Runs root to walk for sessions and run records. Defaults to the workspace's runs/.",
    )
    rebuild.add_argument(
        "--json",
        action="store_true",
        help="Emit the rebuild summary as JSON.",
    )
    rebuild.set_defaults(func=rebuild_main)

    # PR-3 Phase 1: application outcome log viewer.
    from agentic_swmm.commands.memory_health import add_subparser as _add_health

    _add_health(sub)

    # PR-4 Phase 1: explicit archive/restore verbs.
    from agentic_swmm.commands.memory_archive_cmd import add_subparser as _add_archive

    _add_archive(sub)

    # Issue #204: non-destructive repair for the session database (memory.sqlite).
    repair = sub.add_parser(
        "repair-sessions",
        help=(
            "Back up the cross-session SQLite store and rebuild it from "
            "the raw agent_trace.jsonl files under runs/. Non-destructive: "
            "the original file is moved to memory.sqlite.corrupt-<utc>."
        ),
    )
    repair.add_argument(
        "--runs-root",
        type=Path,
        default=None,
        help=(
            "Override the runs directory walked for agent_trace.jsonl. "
            "Defaults to $AISWMM_RUNS_ROOT or <repo>/runs."
        ),
    )
    repair.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Scan + print what would be backed up and rebuilt, write "
            "nothing. Useful for sanity-checking before an irreversible "
            "rebuild."
        ),
    )
    repair.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Skip the interactive y/N confirmation prompt. Required for "
            "scripted / non-interactive use."
        ),
    )
    repair.set_defaults(func=repair_sessions_main)


def _dispatch(args: argparse.Namespace) -> int:
    """Route to the subcommand; the summarise-memory mode is gone (2026-09-26)."""
    if getattr(args, "memory_command", None):
        return int(args.func(args) or 0)
    raise SystemExit(
        "aiswmm memory needs a subcommand: show <case>, proposals, promote <id>, "
        "reject <id>, rebuild, health, archive, restore or repair-sessions."
    )


def show_main(args: argparse.Namespace) -> int:
    """Print the per-case memory card. Thin verb -> ``memory.card`` renderer."""
    from agentic_swmm.memory.card import render_case_card

    memory_dir = (
        args.memory_dir.expanduser().resolve()
        if getattr(args, "memory_dir", None)
        else resolve_memory_dir()
    )
    print(render_case_card(memory_dir, args.case))
    return 0


def proposals_main(args: argparse.Namespace) -> int:
    """``aiswmm memory proposals [--all] [--json]``: what awaits a decision."""
    from agentic_swmm.memory.proposals import list_proposals, proposals_dir

    rows = list_proposals(include_decided=bool(getattr(args, "all", False)))
    if getattr(args, "json", False):
        print(json.dumps(
            [
                {
                    "id": p.id, "kind": p.kind, "status": p.status, "target": p.target,
                    "created_utc": p.created_utc, "decided_utc": p.decided_utc, "reason": p.reason,
                    "path": str(p.path), "evidence": p.evidence,
                }
                for p in rows
            ],
            indent=2, ensure_ascii=False,
        ))
        return 0
    if not rows:
        print(f"no open proposals under {proposals_dir()}")
        return 0
    print(f"{'id':<4} {'status':<9} {'kind':<6} target")
    for p in rows:
        print(f"{p.id:<4} {p.status:<9} {p.kind:<6} {p.target}")
        first = p.addition.strip().splitlines()[0] if p.addition.strip() else ""
        if p.kind == "fact":
            first = next((line for line in p.addition.splitlines() if line.startswith("text: ")), first)
        print(f"     {first[:110]}")
    print("decide with: aiswmm memory promote <id> | aiswmm memory reject <id> --reason ...")
    return 0


def promote_main(args: argparse.Namespace) -> int:
    """``aiswmm memory promote <id>``: apply the proposal to its target file."""
    from agentic_swmm.memory.proposals import promote

    try:
        result = promote(str(args.proposal_id))
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not result.get("ok"):
        print(f"not promoted: {result.get('reason')}", file=sys.stderr)
        return 1
    print(f"promoted {result['id']} -> {result['target']} (now review with git diff, then commit or open a PR)")
    return 0


def reject_main(args: argparse.Namespace) -> int:
    """``aiswmm memory reject <id> [--reason]``: decline the proposal for good."""
    from agentic_swmm.memory.proposals import reject

    try:
        result = reject(str(args.proposal_id), reason=str(getattr(args, "reason", "") or ""))
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not result.get("ok"):
        print(f"not rejected: {result.get('reason')}", file=sys.stderr)
        return 1
    print(f"rejected {result['id']}" + (f": {result['reason']}" if result.get("reason") else ""))
    return 0


def rebuild_main(args: argparse.Namespace) -> int:
    """``aiswmm memory rebuild``: the database is a projection; rebuild it."""
    from agentic_swmm.memory.store import rebuild, table_counts

    memory_dir = resolve_memory_dir()
    runs_dir = args.rebuild_runs_dir.expanduser().resolve() if getattr(args, "rebuild_runs_dir", None) else resolve_runs_dir()
    result = rebuild(memory_dir, runs_dir if runs_dir.is_dir() else None)
    result["counts"] = table_counts(Path(result["db_path"]))
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    print(f"db path:  {result['db_path']}")
    if result.get("backup"):
        print(f"previous: {result['backup']}")
    for table, count in result["counts"].items():
        print(f"  {table:16} {count} row(s)")
    sessions = result.get("sessions") or {}
    if sessions:
        print(f"sessions rebuilt from runs/: {sessions.get('rebuilt', 0)} ({sessions.get('failures', 0)} failure(s))")
    return 0


# Issue #204: repair-sessions — the backup/rebuild engine lives in
# agentic_swmm/memory/session_repair.py (moved out of this verb module
# in the 2026-07 architecture pass); this module keeps the argparse
# surface plus these re-imports for existing callers.
from agentic_swmm.memory.session_repair import (  # noqa: E402
    _preview_repair,
    repair_sessions_db,
)


def repair_sessions_main(args: argparse.Namespace) -> int:
    """CLI entry point for ``aiswmm memory repair-sessions``.

    Resolves the runs dir from ``--runs-root`` -> ``$AISWMM_RUNS_ROOT``
    -> ``<repo>/runs``, calls :func:`repair_sessions_db`, and prints a
    human-readable summary. Returns 0 on success, 1 when the helper
    reports ``ok == False``.

    Issue #212: gated by ``--dry-run`` (preview, write nothing) and
    an interactive y/N prompt unless ``--yes`` is passed. ``--yes``
    is required for non-interactive / scripted callers.
    """
    runs_root: Path
    if getattr(args, "runs_root", None) is not None:
        runs_root = args.runs_root.expanduser().resolve()
    else:
        runs_root = resolve_runs_dir()
    from agentic_swmm.memory.session_sync import default_db_path

    db_path = default_db_path()

    # ---- --dry-run: walk the same paths but write nothing.
    if getattr(args, "dry_run", False):
        preview = _preview_repair(runs_root, db_path)
        print(f"runs dir: {runs_root}")
        print(f"db path:  {db_path}")
        backup = preview["would_back_up_to"]
        if backup:
            print(f"would back up corrupt store -> {backup}")
        else:
            print("no prior memory.sqlite to back up (would fresh-rebuild)")
        print(
            f"would rebuild {preview['would_rebuild_sessions']} session(s)"
        )
        print("(dry run — no files written)")
        return 0

    # ---- Default interactive confirm. Skip when --yes or when stdin
    # is not a TTY (the caller is scripted but forgot --yes — refuse
    # rather than silently destroy data).
    if not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            print(
                "repair-sessions is destructive; refusing to run without "
                "--yes on a non-interactive stdin.",
                file=sys.stderr,
            )
            return 1
        print(f"runs dir: {runs_root}")
        print(f"db path:  {db_path}")
        print(
            "This will move the current memory.sqlite (if any) to a "
            ".corrupt-<utc> backup and rebuild from runs/*/agent_trace.jsonl."
        )
        response = input("Proceed? [y/N]: ").strip().lower()
        if response not in {"y", "yes"}:
            print("aborted; nothing was written.")
            return 0

    result = repair_sessions_db(runs_root, db_path=db_path)

    backup = result.get("backup")
    rebuilt = int(result.get("sessions_rebuilt") or 0)
    messages = int(result.get("messages_rebuilt") or 0)
    tool_events = int(result.get("tool_events_rebuilt") or 0)
    failures = list(result.get("failures") or [])

    print(f"runs dir: {runs_root}")
    print(f"db path:  {db_path}")
    if backup:
        print(f"backed up corrupt store -> {backup}")
    else:
        print("no prior memory.sqlite to back up (fresh rebuild)")
    print(
        f"rebuilt {rebuilt} session(s), {messages} message(s), "
        f"{tool_events} tool event(s)"
    )
    if failures:
        print(f"skipped {len(failures)} session(s):")
        for line in failures[:20]:
            print(f"  - {line}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")

    return 0 if result.get("ok") else 1
