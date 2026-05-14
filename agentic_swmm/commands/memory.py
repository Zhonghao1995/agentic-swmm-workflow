from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from agentic_swmm.utils.paths import repo_root, require_dir, script_path
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
    """
    from agentic_swmm.memory import facts as _facts_mod

    result = _facts_mod.promote_facts(editor=getattr(args, "editor", None))
    if not result.get("ok"):
        print(result.get("reason", "promote-facts failed"))
        return 1
    print(result.get("reason", "promote-facts: done"))
    return 0


def _resolve_memory_dir() -> Path:
    override = os.environ.get("AISWMM_MEMORY_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "memory" / "modeling-memory"


def _resolve_rag_dir() -> Path:
    override = os.environ.get("AISWMM_RAG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "memory" / "rag-memory"


def _resolve_runs_dir() -> Path:
    override = os.environ.get("AISWMM_RUNS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "runs"


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

    memory_dir = _resolve_memory_dir()
    rag_dir = _resolve_rag_dir()
    runs_dir = _resolve_runs_dir()
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
        repo_root() / "skills" / "swmm-rag-memory" / "scripts" / "build_memory_corpus.py"
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
