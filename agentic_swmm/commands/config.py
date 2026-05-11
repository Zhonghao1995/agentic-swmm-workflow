from __future__ import annotations

import argparse
import json

from agentic_swmm.config import config_path, load_config, set_config_value


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("config", help="View or update local aiswmm runtime config.")
    child = parser.add_subparsers(dest="config_command", required=True)

    show = child.add_parser("show", help="Print the effective runtime config.")
    show.set_defaults(func=show_config)

    path = child.add_parser("path", help="Print the user config path.")
    path.set_defaults(func=print_path)

    setter = child.add_parser("set", help="Set a dotted config value.")
    setter.add_argument("key", help="Dotted key, for example openai.model")
    setter.add_argument("value", help="Value to store.")
    setter.set_defaults(func=set_value)


def show_config(args: argparse.Namespace) -> int:
    config = load_config()
    print(json.dumps(config.values, indent=2))
    return 0


def print_path(args: argparse.Namespace) -> int:
    print(config_path())
    return 0


def set_value(args: argparse.Namespace) -> int:
    config = set_config_value(args.key, args.value)
    print(f"set {args.key} in {config.path}")
    return 0
