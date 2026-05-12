from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from agentic_swmm.commands import agent


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("chat", help="Compatibility alias for the unified Agentic SWMM runtime.")
    parser.add_argument("prompt", nargs="*", help="Prompt text. If omitted, starts the interactive agent runtime.")
    parser.add_argument("--provider", choices=["openai"], help="Provider to use. Defaults to config provider.default.")
    parser.add_argument("--model", help="Model override for this request.")
    parser.add_argument("--session-id", help="Stable session id. Defaults to a timestamped id.")
    parser.add_argument("--session-dir", help="Directory for trace, tool outputs, and final report.")
    parser.add_argument("--max-steps", type=int, default=8, help="Maximum tool calls to execute.")
    parser.add_argument("--verbose", action="store_true", help="Show full planner/tool details in the terminal.")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    forwarded = SimpleNamespace(
        goal=list(args.prompt),
        planner="openai",
        provider=args.provider,
        model=args.model,
        session_id=args.session_id,
        session_dir=Path(args.session_dir) if args.session_dir else None,
        dry_run=False,
        interactive=not bool(args.prompt),
        max_steps=args.max_steps,
        verbose=args.verbose,
    )
    return agent.main(forwarded)
