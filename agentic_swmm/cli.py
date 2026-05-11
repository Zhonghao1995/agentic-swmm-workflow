from __future__ import annotations

import argparse
import sys

from agentic_swmm import __version__
from agentic_swmm.commands import agent, audit, chat, config, demo, doctor, mcp, memory, model, plot, run, setup, skill


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-swmm",
        description="Unified CLI for reproducible and auditable Agentic SWMM workflows.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    chat.register(subparsers)
    agent.register(subparsers)
    model.register(subparsers)
    config.register(subparsers)
    setup.register(subparsers)
    mcp.register(subparsers)
    skill.register(subparsers)
    doctor.register(subparsers)
    run.register(subparsers)
    audit.register(subparsers)
    plot.register(subparsers)
    memory.register(subparsers)
    demo.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["chat"]
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
