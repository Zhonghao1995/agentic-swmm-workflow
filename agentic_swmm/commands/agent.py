"""``aiswmm agent`` subcommand: argparse + dispatch only.

The actual behaviour lives in two sibling modules:

- ``agentic_swmm.agent.runtime_loop`` — interactive shell + OpenAI
  planner turn loop.
- ``agentic_swmm.agent.single_shot`` — non-interactive rule-planner
  flow plus the historical tool-dispatch helpers.

This split lands as a no-behaviour-change move (PRD: Runtime UX).
``_find_repo_inp`` is re-exported here for backwards compatibility with
``tests/test_agentic_swmm_cli.py``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentic_swmm.agent.permissions_profile import Profile, profile_from_string
from agentic_swmm.agent.runtime_loop import run_interactive_shell
from agentic_swmm.agent.single_shot import _find_repo_inp, run_single_shot

__all__ = [
    "register",
    "main",
    "resolve_profile_string",
    "resolve_profile_from_args",
    "_find_repo_inp",
]


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("agent", help="Run the constrained local aiswmm executor.")
    parser.add_argument("goal", nargs="*", help="Goal for the local executor.")
    parser.add_argument("--planner", choices=["rule", "openai"], default="rule", help="Planner backend. Defaults to the deterministic rule planner.")
    parser.add_argument("--provider", choices=["openai"], help="Provider to use with --planner openai. Defaults to config provider.default.")
    parser.add_argument("--model", help="Model override for --planner openai.")
    parser.add_argument("--session-id", help="Stable session id. Defaults to a timestamped id.")
    parser.add_argument("--session-dir", type=Path, help="Directory for trace, tool outputs, and final report.")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; do not execute tools.")
    parser.add_argument("--interactive", action="store_true", help="Start an interactive agent shell; each prompt is executed with tool access.")
    parser.add_argument("--max-steps", type=int, default=16, help="Maximum tool calls to execute.")
    parser.add_argument("--verbose", action="store_true", help="Show full planner/tool details in the terminal.")
    parser.add_argument(
        "--safe",
        action="store_true",
        help=(
            "Permission profile SAFE: prompt for every tool call. "
            "Default is QUICK (auto-approves read-only tools like "
            "read_file, list_*, search_files, inspect_plot_options)."
        ),
    )
    # ``--quick`` is the legacy spelling of the now-default profile. Keep
    # it parsable for one release so existing scripts / docs don't break,
    # but hide it from --help so new users learn the --safe spelling.
    parser.add_argument(
        "--quick",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(func=main)


def resolve_profile_string(args: argparse.Namespace) -> str:
    """Resolve the user's requested profile to ``"quick"`` / ``"safe"``.

    Precedence rules:

    - ``--safe`` selects ``"safe"`` and wins over ``--quick``.
    - ``--quick`` alone (without ``--safe``) selects ``"quick"``.
    - Neither flag → ``"quick"`` (the new default).
    - Both flags → ``"safe"`` plus a single-line stderr warning so the
      user knows ``--quick`` was ignored.
    """
    want_safe = bool(getattr(args, "safe", False))
    want_quick = bool(getattr(args, "quick", False))
    if want_safe and want_quick:
        print(
            "warning: --safe overrides --quick; using SAFE profile.",
            file=sys.stderr,
        )
        return "safe"
    if want_safe:
        return "safe"
    if want_quick:
        return "quick"
    return "quick"


def resolve_profile_from_args(args: argparse.Namespace) -> Profile:
    """Convenience: ``resolve_profile_string`` composed with ``profile_from_string``.

    Downstream callers (``runtime_loop``, ``single_shot``) consume the
    enum directly, so they should prefer this helper over re-checking
    ``args.safe`` / ``args.quick``.
    """
    return profile_from_string(resolve_profile_string(args))


def main(args: argparse.Namespace) -> int:
    if args.interactive:
        return run_interactive_shell(args)
    return run_single_shot(args)
