from __future__ import annotations

import argparse
from pathlib import Path

from agentic_swmm.config import skills_registry_path
from agentic_swmm.runtime.registry import discover_skills, load_skill_registry


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("skill", help="Inspect local Agentic SWMM skills.")
    child = parser.add_subparsers(dest="skill_command", required=True)
    list_parser = child.add_parser("list", help="List repository skills.")
    list_parser.add_argument("--registry", action="store_true", help="Read the user runtime skill registry.")
    list_parser.set_defaults(func=list_skills)


def list_skills(args: argparse.Namespace) -> int:
    records = load_skill_registry() if args.registry else discover_skills()
    for record in records:
        status = "enabled" if record.get("enabled", True) else "disabled"
        print(f"{record['name']} ({status}) - {record['path']}")
    if args.registry:
        print(f"registry: {skills_registry_path()}")
    return 0
