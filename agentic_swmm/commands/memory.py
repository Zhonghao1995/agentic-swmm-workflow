from __future__ import annotations

import argparse
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
