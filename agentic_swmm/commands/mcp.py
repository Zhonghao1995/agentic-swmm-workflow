from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_swmm.config import mcp_registry_path
from agentic_swmm.runtime.registry import discover_mcp_servers, load_mcp_registry


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("mcp", help="Inspect local Agentic SWMM MCP servers.")
    child = parser.add_subparsers(dest="mcp_command", required=True)
    list_parser = child.add_parser("list", help="List repository MCP servers.")
    list_parser.add_argument("--json", action="store_true", help="Print machine-readable server records.")
    list_parser.add_argument("--registry", action="store_true", help="Read the user runtime MCP registry.")
    list_parser.set_defaults(func=list_servers)


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
