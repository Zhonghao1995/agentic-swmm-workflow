from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from agentic_swmm.agent.experimental_providers import (
    available_provider_choices,
    provider_help_text,
)
from agentic_swmm.commands import agent


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("chat", help="Compatibility alias for the unified Agentic SWMM runtime.")
    parser.add_argument("prompt", nargs="*", help="Prompt text. If omitted, starts the interactive agent runtime.")
    parser.add_argument(
        "--provider",
        choices=available_provider_choices(),
        help=provider_help_text("Provider to use. Defaults to config provider.default."),
    )
    parser.add_argument("--model", help="Model override for this request.")
    parser.add_argument("--session-id", help="Stable session id. Defaults to a timestamped id.")
    parser.add_argument("--session-dir", help="Directory for trace, tool outputs, and final report.")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=40,
        help=(
            "Maximum tool calls per turn. Default 40 leaves ~25 steps for real "
            "operations after the planner's ~15-step introspection overhead "
            "(list_skills / read_skill / list_mcp_tools / select_skill). Bump "
            "higher for chains that include plot_run AND map_run AND audit. "
            "Lower (e.g. --max-steps 16) if you want a tighter token budget."
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="Show full planner/tool details in the terminal.")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    forwarded = SimpleNamespace(
        goal=list(args.prompt),
        planner="llm",
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
