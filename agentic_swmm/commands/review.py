"""``aiswmm review`` — deterministic design-review / code-compliance checker.

Thin argparse wrapper that dispatches to
``skills/swmm-design-review/scripts/design_review.py``.

Per CONTEXT.md: CLI verb modules MUST stay thin — argparse + help-text
+ a 1-2 line call into business logic (the script subprocess).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.utils.paths import require_dir, script_path
from agentic_swmm.utils.subprocess_runner import python_command, run_command


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "review",
        help="Run the design-review / code-compliance checklist against a completed run.",
    )
    parser.add_argument("--run-dir", type=Path, required=True, help="Run directory to review.")
    parser.add_argument(
        "--rules",
        type=Path,
        default=None,
        help="Path to a custom YAML rulebook (default: bundled GB 50014 template).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory for review artifacts (default: <run-dir>/09_review/).",
    )
    register_example_flag(parser, example_text="aiswmm review --run-dir runs/<case>")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    run_dir = require_dir(args.run_dir, "run directory")
    cmd = python_command(
        script_path("skills", "swmm-design-review", "scripts", "design_review.py"),
        "--run-dir", str(run_dir),
    )
    if args.rules:
        cmd.extend(["--rules", str(args.rules)])
    if args.out_dir:
        cmd.extend(["--out-dir", str(args.out_dir)])
    result = run_command(cmd)
    if result.stdout.strip():
        print(result.stdout.strip())
    return result.return_code
