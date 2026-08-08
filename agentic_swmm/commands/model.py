from __future__ import annotations

import argparse

from agentic_swmm.agent.experimental_providers import (
    available_provider_choices,
    provider_help_text,
)
from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.config import set_config_value


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("model", help="View or choose the default provider and model.")
    parser.add_argument(
        "--provider",
        choices=available_provider_choices(),
        help=provider_help_text("Default provider for agent planner commands."),
    )
    parser.add_argument("--model", help="Default model for the selected provider.")
    register_example_flag(parser, example_text="aiswmm model --provider openai")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    from agentic_swmm.providers.selection import resolve_selection

    if args.provider:
        set_config_value("provider.default", args.provider)
    if args.model:
        provider = args.provider or resolve_selection().route
        set_config_value(f"{provider}.model", args.model)

    selection = resolve_selection()
    print(f"provider: {selection.route}")
    print(f"model: {selection.model}")
    return 0
