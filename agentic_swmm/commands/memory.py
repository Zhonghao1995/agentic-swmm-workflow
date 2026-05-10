from __future__ import annotations

import argparse
from pathlib import Path

from agentic_swmm.utils.paths import repo_root, require_dir, script_path
from agentic_swmm.utils.subprocess_runner import append_trace, python_command, run_command


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("memory", help="Summarize audited runs into modeling-memory outputs.")
    parser.add_argument("--runs-dir", required=True, type=Path, help="Directory containing audited run folders.")
    parser.add_argument("--out-dir", type=Path, help="Output directory. Defaults to memory/modeling-memory.")
    parser.add_argument("--obsidian-dir", type=Path, help="Optional Obsidian export directory.")
    parser.set_defaults(func=main)


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
