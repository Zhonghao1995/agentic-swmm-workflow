from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.utils.paths import (
    repo_root,
    resource_root,
    require_dir,
    resolve_memory_dir,
    resolve_runs_dir,
    script_path,
)
from agentic_swmm.utils.subprocess_runner import append_trace, python_command, run_command


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "memory",
        help="Summarize audited runs into modeling-memory outputs, or manage curated project facts.",
    )
    # Backwards-compatible: ``aiswmm memory --runs-dir ...`` still runs the
    # summarise-memory pipeline.
    parser.add_argument(
        "--runs-dir",
        type=Path,
        help="Directory containing audited run folders (summarise-memory mode).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Output directory. Defaults to memory/modeling-memory.",
    )
    parser.add_argument(
        "--obsidian-dir",
        type=Path,
        help="Optional Obsidian export directory.",
    )
    register_example_flag(
        parser, example_text="aiswmm memory --runs-dir runs"
    )
    parser.set_defaults(func=_dispatch)

    sub = parser.add_subparsers(dest="memory_command")
    promote = sub.add_parser(
        "promote-facts",
        help="Open the staged facts file in $EDITOR, then append to facts.md.",
    )
    promote.add_argument(
        "--editor",
        type=str,
        default=None,
        help="Editor binary to invoke (overrides $EDITOR).",
    )
    promote.set_defaults(func=promote_facts_main)

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
        help="Modeling-memory directory. Defaults to memory/modeling-memory.",
    )
    show.set_defaults(func=show_main)

    compact = sub.add_parser(
        "compact",
        help=(
            "Force a full decay pass over lessons_learned.md and rebuild the "
            "RAG corpus. Retired patterns are moved to lessons_archived.md."
        ),
    )
    compact.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON DecayReport on stdout.",
    )
    compact.add_argument(
        "--no-rag",
        action="store_true",
        help="Skip the RAG corpus rebuild; only refresh lifecycle metadata.",
    )
    compact.set_defaults(func=compact_main)

    # Expert-only: LLM-driven reflection (ME-3). Registered as a memory
    # subcommand so it lives under ``aiswmm memory reflect``; it is
    # NOT a ToolSpec and NOT an MCP tool — see PRD
    # memory-evolution-with-forgetting governance.
    from agentic_swmm.commands.expert import memory_reflect as expert_memory_reflect

    expert_memory_reflect.add_subparser(sub)

    # Round 7: one-shot migration of negative_lessons.jsonl -> .md.
    migrate_neg = sub.add_parser(
        "migrate-negative-lessons-md",
        help=(
            "Convert negative_lessons.jsonl into negative_lessons.md (idempotent). "
            "After migration the audit hook writes new sections to the markdown store."
        ),
    )
    migrate_neg.add_argument(
        "--archive",
        action="store_true",
        help="Run apply_decay + archive_retired on the markdown after migration.",
    )
    migrate_neg.set_defaults(func=migrate_negative_lessons_md_main)

    # PR-3 Phase 1: application outcome log viewer.
    from agentic_swmm.commands.memory_health import add_subparser as _add_health

    _add_health(sub)

    # PR-4 Phase 1: explicit archive/restore verbs.
    from agentic_swmm.commands.memory_archive_cmd import add_subparser as _add_archive

    _add_archive(sub)

    # Issue #204: non-destructive repair for runs/sessions.sqlite.
    repair = sub.add_parser(
        "repair-sessions",
        help=(
            "Back up the cross-session SQLite store and rebuild it from "
            "the raw agent_trace.jsonl files under runs/. Non-destructive: "
            "the original file is moved to sessions.sqlite.corrupt-<utc>."
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
    """Route between the legacy summarise-memory mode and new subcommands."""
    if getattr(args, "memory_command", None):
        return int(args.func(args) or 0)
    if args.runs_dir is None:
        raise SystemExit(
            "Either --runs-dir (summarise-memory mode) or a subcommand like "
            "`promote-facts` is required."
        )
    return main(args)


def show_main(args: argparse.Namespace) -> int:
    """Print the per-case memory card. Thin verb -> ``memory.card`` renderer."""
    from agentic_swmm.memory.card import render_case_card

    memory_dir = (
        args.memory_dir.expanduser().resolve()
        if getattr(args, "memory_dir", None)
        else repo_root() / "memory" / "modeling-memory"
    )
    print(render_case_card(memory_dir, args.case))
    return 0


def main(args: argparse.Namespace) -> int:
    runs_dir = require_dir(args.runs_dir, "runs directory")
    out_dir = args.out_dir.expanduser().resolve() if args.out_dir else repo_root() / "memory" / "modeling-memory"
    script = script_path("skills", "swmm-modeling-memory", "scripts", "summarize_memory.py")
    command = python_command(script, "--runs-dir", str(runs_dir), "--out-dir", str(out_dir))
    if args.obsidian_dir:
        command.extend(["--obsidian-dir", str(args.obsidian_dir.expanduser().resolve())])
    result = run_command(command)
    append_trace(out_dir / "command_trace.json", result, stage="memory")
    print(result.stdout.strip())
    return result.return_code


def promote_facts_main(args: argparse.Namespace) -> int:
    """Drive the user-facing ``aiswmm memory promote-facts`` flow.

    Hands control off to the user's ``$EDITOR`` on the staging file
    and, if the editor exits cleanly, appends the (possibly edited)
    content to ``facts.md`` then truncates staging.

    PRD-08 A.3 (audit #32): when the staging file is empty, emit a
    typed remediation stanza pointing at ``record_fact`` rather than
    the bare "staging is empty" line.
    """
    from agentic_swmm.agent.error_remediation import staged_facts_empty
    from agentic_swmm.memory import facts as _facts_mod

    result = _facts_mod.promote_facts(editor=getattr(args, "editor", None))
    if not result.get("ok"):
        print(result.get("reason", "promote-facts failed"))
        return 1
    reason = result.get("reason", "promote-facts: done")
    if reason == "staging is empty":
        staging_path = result.get("staging_md")
        staging = Path(staging_path) if staging_path else None
        err = staged_facts_empty(staging_md=staging)
        sys.stderr.write(err.format_for_stderr() + "\n")
        return 0
    print(reason)
    return 0


def _resolve_rag_dir() -> Path:
    override = os.environ.get("AISWMM_RAG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "memory" / "rag-memory"


def _resolve_evolution_config() -> Path:
    override = os.environ.get("AISWMM_MEMORY_EVOLUTION_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "agent" / "memory" / "curated" / "memory_evolution_config.md"


def _print_report_table(report_dict: dict) -> None:
    """Pretty-print a DecayReport-as-dict for humans."""
    print("\nDecay report")
    print("-" * 40)
    rows = (
        ("promoted (-> active)", report_dict.get("promoted", [])),
        ("demoted  (-> dormant)", report_dict.get("demoted", [])),
        ("retired  (-> archive)", report_dict.get("retired", [])),
        ("unchanged", report_dict.get("unchanged", [])),
    )
    for label, names in rows:
        names_list = list(names) if names else []
        if names_list:
            joined = ", ".join(names_list)
        else:
            joined = "(none)"
        print(f"  {label:24s} {len(names_list):3d}  {joined}")
    print("-" * 40)


def compact_main(args: argparse.Namespace) -> int:
    """Force a full decay pass + RAG rebuild.

    Returns 0 with a printed (or JSON) ``DecayReport`` table on
    success.
    """
    from agentic_swmm.memory.lessons_lifecycle import apply_decay, load_config

    memory_dir = resolve_memory_dir()
    rag_dir = _resolve_rag_dir()
    runs_dir = resolve_runs_dir()
    config_path = _resolve_evolution_config()
    lessons_path = memory_dir / "lessons_learned.md"
    archive_path = memory_dir / "lessons_archived.md"

    if not lessons_path.is_file():
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "lessons_learned.md not found",
                    "lessons_path": str(lessons_path),
                }
            )
        )
        return 1

    config = load_config(config_path)
    report = apply_decay(lessons_path, archive_path, config)
    report_dict = report.to_dict()

    rag_rebuild: dict | None = None
    if not getattr(args, "no_rag", False):
        rag_rebuild = _rebuild_rag_corpus(memory_dir, rag_dir, runs_dir)

    summary = {
        **report_dict,
        "config": {
            "half_life_days": config.get("half_life_days"),
            "active_threshold": config.get("active_threshold"),
            "dormant_threshold": config.get("dormant_threshold"),
        },
        "lessons_path": str(lessons_path),
        "archive_path": str(archive_path),
    }
    if rag_rebuild is not None:
        summary["rag_rebuild"] = rag_rebuild

    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        _print_report_table(report_dict)
        if rag_rebuild is not None:
            status = "ok" if rag_rebuild.get("returncode") == 0 else "failed"
            print(f"RAG corpus rebuild: {status}")
        print(f"\nlessons: {lessons_path}")
        print(f"archive: {archive_path}")
    return 0


def _rebuild_rag_corpus(memory_dir: Path, rag_dir: Path, runs_dir: Path) -> dict:
    """Invoke ``build_memory_corpus.py`` and capture its outcome.

    Returns a small status dict ``{returncode, stderr_tail}`` so the
    CLI can surface failure without raising — corrupt RAG rebuilds
    should not nuke a successful decay pass.
    """
    script = (
        resource_root() / "skills" / "swmm-rag-memory" / "scripts" / "build_memory_corpus.py"
    )
    if not script.is_file():
        return {"returncode": 0, "skipped": True, "reason": "build script missing"}
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--memory-dir",
                str(memory_dir),
                "--runs-dir",
                str(runs_dir),
                "--out-dir",
                str(rag_dir),
                "--repo-root",
                str(repo_root()),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {"returncode": proc.returncode, "stderr_tail": (proc.stderr or "")[-400:]}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"returncode": 1, "stderr_tail": str(exc)}


def migrate_negative_lessons_md_main(args: argparse.Namespace) -> int:
    """Drive ``aiswmm memory migrate-negative-lessons-md``.

    Migrates the existing JSONL store to the markdown lifecycle file and
    optionally runs the decay/archive pass when ``--archive`` is set.
    """
    from agentic_swmm.memory.negative_lessons_markdown import (
        apply_decay,
        archive_retired,
        migrate_jsonl_to_md,
    )

    memory_dir = resolve_memory_dir()
    jsonl_path = memory_dir / "negative_lessons.jsonl"
    md_path = memory_dir / "negative_lessons.md"
    archive_path = memory_dir / "negative_lessons_archived.md"

    if not jsonl_path.is_file():
        print(
            json.dumps(
                {
                    "ok": True,
                    "migrated": 0,
                    "reason": "no negative_lessons.jsonl to migrate",
                    "jsonl_path": str(jsonl_path),
                }
            )
        )
        return 0

    migrated = migrate_jsonl_to_md(jsonl_path, md_path)
    summary: dict = {
        "ok": True,
        "migrated": migrated,
        "jsonl_path": str(jsonl_path),
        "md_path": str(md_path),
    }
    if getattr(args, "archive", False):
        counts = apply_decay(md_path)
        archived = archive_retired(md_path, archive_path)
        summary["decay_counts"] = counts
        summary["archived"] = archived
        summary["archive_path"] = str(archive_path)
    print(json.dumps(summary, indent=2, sort_keys=True))
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

    db_path = runs_root / "sessions.sqlite"

    # ---- --dry-run: walk the same paths but write nothing.
    if getattr(args, "dry_run", False):
        preview = _preview_repair(runs_root, db_path)
        print(f"runs dir: {runs_root}")
        print(f"db path:  {db_path}")
        backup = preview["would_back_up_to"]
        if backup:
            print(f"would back up corrupt store -> {backup}")
        else:
            print("no prior sessions.sqlite to back up (would fresh-rebuild)")
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
            "This will move the current sessions.sqlite (if any) to a "
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
        print("no prior sessions.sqlite to back up (fresh rebuild)")
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
