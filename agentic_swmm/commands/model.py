from __future__ import annotations

import argparse

from agentic_swmm.agent.experimental_providers import available_provider_choices
from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.config import load_config, set_config_value


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("model", help="View or choose the default provider and model.")
    # Issue #182: provider choices honour the experimental-providers gate.
    parser.add_argument("--provider", choices=available_provider_choices(), help="Default provider for agent planner commands.")
    parser.add_argument("--model", help="Default model for the selected provider.")
    register_example_flag(parser, example_text="aiswmm model --provider openai")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    if args.provider:
        set_config_value("provider.default", args.provider)
    if args.model:
        provider = args.provider or load_config().get("provider.default", "openai")
        set_config_value(f"{provider}.model", args.model)

    config = load_config()
    provider = config.get("provider.default", "openai")
    model = config.get(f"{provider}.model")
    print(f"provider: {provider}")
    print(f"model: {model}")
    return 0
