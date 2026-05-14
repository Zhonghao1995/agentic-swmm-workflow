from __future__ import annotations

import argparse
import sys

from agentic_swmm import __version__
from agentic_swmm.commands import agent, audit, capabilities, config, demo, doctor, mcp, memory, model, plot, run, setup, skill, uncertainty
from agentic_swmm.commands.expert import calibration as expert_calibration
from agentic_swmm.commands.expert import pour_point as expert_pour_point
from agentic_swmm.commands.expert import publish as expert_publish
from agentic_swmm.commands.expert import thresholds as expert_thresholds


COMMANDS = {
    "agent",
    "model",
    "config",
    "capabilities",
    "setup",
    "mcp",
    "skill",
    "doctor",
    "run",
    "audit",
    "plot",
    "memory",
    "demo",
    # Uncertainty integration deliverable (issue #55). Lives at the top
    # level so the default-router does not punt it to the agent — it is
    # a deterministic CLI surface over a pure function.
    "uncertainty",
    # Expert-only commands (PRD-Z). Listed here so the default-router
    # does not punt them to the agent; the agent itself has no
    # ToolSpec entries for these names.
    "calibration",
    "pour_point",
    "thresholds",
    "publish",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-swmm",
        description="Unified CLI for reproducible and auditable Agentic SWMM workflows.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    agent.register(subparsers)
    model.register(subparsers)
    config.register(subparsers)
    capabilities.register(subparsers)
    setup.register(subparsers)
    mcp.register(subparsers)
    skill.register(subparsers)
    doctor.register(subparsers)
    run.register(subparsers)
    audit.register(subparsers)
    plot.register(subparsers)
    memory.register(subparsers)
    demo.register(subparsers)
    # Issue #55 — uncertainty source decomposition (paper-reviewer view).
    uncertainty.register(subparsers)
    # Expert-only commands (PRD-Z). Surfaced as top-level subcommands
    # so the help renders an "expert-only" grouping naturally; none of
    # them is registered as an agent ToolSpec or as an MCP tool.
    expert_calibration.register(subparsers)
    expert_pour_point.register(subparsers)
    expert_thresholds.register(subparsers)
    expert_publish.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    argv = _route_default_to_agent(argv)
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


def _route_default_to_agent(argv: list[str]) -> list[str]:
    if not argv:
        return ["agent", "--planner", "openai", "--interactive"]
    if argv[0] == "chat":
        return ["agent", "--planner", "openai", *argv[1:]] if len(argv) > 1 else ["agent", "--planner", "openai", "--interactive"]
    if argv[0] in COMMANDS:
        if argv[0] == "run" and "--inp" not in argv:
            return ["agent", "--planner", "openai", *argv]
        return argv
    if argv[0] in {"-h", "--help", "--version"}:
        return argv
    if argv[0].startswith("-"):
        if _agent_options_without_goal(argv):
            return ["agent", "--planner", "openai", "--interactive", *argv]
        return ["agent", "--planner", "openai", *argv]
    return ["agent", "--planner", "openai", *argv]


def _agent_options_without_goal(argv: list[str]) -> bool:
    options_with_values = {"--provider", "--model", "--session-id", "--session-dir", "--max-steps"}
    flags = {"--dry-run", "--interactive", "--verbose"}
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in options_with_values:
            index += 2
            continue
        if item in flags:
            index += 1
            continue
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
