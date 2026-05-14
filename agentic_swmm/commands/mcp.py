from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from agentic_swmm.agent.mcp_coverage import build_coverage_matrix, format_coverage_table
from agentic_swmm.config import mcp_registry_path
from agentic_swmm.runtime.registry import discover_mcp_servers, load_mcp_registry


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("mcp", help="Inspect local Agentic SWMM MCP servers.")
    child = parser.add_subparsers(dest="mcp_command", required=True)
    list_parser = child.add_parser("list", help="List repository MCP servers.")
    list_parser.add_argument("--json", action="store_true", help="Print machine-readable server records.")
    list_parser.add_argument("--registry", action="store_true", help="Read the user runtime MCP registry.")
    list_parser.set_defaults(func=list_servers)
    coverage_parser = child.add_parser(
        "coverage",
        help=(
            "Audit ToolSpec -> Python script -> MCP server.tool coverage. "
            "Exits 0 if every subprocess-Python ToolSpec has a matching MCP tool, 1 if any are MISSING."
        ),
    )
    coverage_parser.add_argument("--json", action="store_true", help="Print the matrix as JSON records.")
    coverage_parser.set_defaults(func=coverage_report)


def list_servers(args: argparse.Namespace) -> int:
    records = load_mcp_registry() if args.registry else discover_mcp_servers()
    if args.json:
        print(json.dumps(records, indent=2))
    else:
        for record in records:
            status = "OK" if record["exists"] else "MISSING"
            print(f"{status:7} {record['name']} - {Path(record['entrypoint'])}")
        if args.registry:
            print(f"registry: {mcp_registry_path()}")
    return 0 if all(record["exists"] for record in records) else 1


def coverage_report(args: argparse.Namespace) -> int:
    """Walk the ToolSpec->script->MCP coverage matrix and print it.

    The deep-module helper in ``agentic_swmm.agent.mcp_coverage`` is the
    single source of truth shared with the lock-in pytest gate.
    """

    rows = build_coverage_matrix()
    if getattr(args, "json", False):
        print(json.dumps([asdict(row) for row in rows], indent=2))
    else:
        print(format_coverage_table(rows))
    return 0 if all(row.status == "OK" for row in rows) else 1
