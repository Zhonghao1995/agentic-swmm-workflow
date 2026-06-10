"""``aiswmm report`` — assemble a client-deliverable Word report.

Thin argparse wrapper that dispatches to
``skills/swmm-report/scripts/generate_report.py``.

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
        "report",
        help="Assemble a Word (.docx) deliverable from an audited run directory.",
    )
    parser.add_argument("--run-dir", type=Path, required=True, help="Audited run directory.")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output .docx path (default: <run-dir>/report.docx).",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Path to a custom template YAML (default: bundled default template).",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Override the cover title text.",
    )
    register_example_flag(parser, example_text="aiswmm report --run-dir runs/<case> --out report.docx")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    run_dir = require_dir(args.run_dir, "run directory")
    cmd = python_command(
        script_path("skills", "swmm-report", "scripts", "generate_report.py"),
        "--run-dir", str(run_dir),
    )
    if args.out:
        cmd.extend(["--out", str(args.out)])
    if args.template:
        cmd.extend(["--template", str(args.template)])
    if args.title:
        cmd.extend(["--title", args.title])
    result = run_command(cmd)
    if result.stdout.strip():
        print(result.stdout.strip())
    return result.return_code
