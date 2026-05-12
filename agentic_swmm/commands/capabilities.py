from __future__ import annotations

import argparse
import json

from agentic_swmm.agent.policy import capability_summary
from agentic_swmm.agent.tool_registry import AgentToolRegistry


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("capabilities", help="Show Agentic SWMM runtime permissions and available agent tools.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    registry = AgentToolRegistry()
    summary = capability_summary(registry.sorted_names())
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print("Agentic SWMM runtime capabilities")
    print(f"- filesystem read: {summary['filesystem']['read']}")
    print(f"- filesystem write: {summary['filesystem']['write']}")
    print(f"- arbitrary shell: {summary['filesystem']['arbitrary_shell']}")
    print(f"- external INP import: {summary['swmm']['run_external_inp_import']}")
    print(f"- web research: {summary['web']['enabled']} ({', '.join(summary['web']['tools'])})")
    print(f"- MCP tools: {summary['mcp']['enabled']}")
    print("- tools:")
    for name in summary["tools"]:
        print(f"  - {name}")
    return 0
