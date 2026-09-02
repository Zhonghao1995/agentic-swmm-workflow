from __future__ import annotations

import argparse
from pathlib import Path

from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.config import skills_registry_path
from agentic_swmm.runtime.registry import discover_skills, load_skill_registry


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("skill", help="Inspect local Agentic SWMM skills.")
    register_example_flag(parser, example_text="aiswmm skill list")
    child = parser.add_subparsers(dest="skill_command", required=True)
    list_parser = child.add_parser("list", help="List repository skills.")
    list_parser.add_argument("--registry", action="store_true", help="Read the user runtime skill registry.")
    list_parser.set_defaults(func=list_skills)
    show_parser = child.add_parser("show", help="Print one skill's description and contract (its SKILL.md).")
    show_parser.add_argument("name", help="Skill name, e.g. swmm-canada.")
    show_parser.add_argument("--full", action="store_true", help="Print the whole SKILL.md instead of the first 60 lines.")
    show_parser.set_defaults(func=show_skill)


def show_skill(args: argparse.Namespace) -> int:
    """``aiswmm skill show <name>``: the promise "Inspect bundled skills" kept.

    Live finding F-41 (2026-09-02): only ``list`` existed, so a user who
    wanted to read what a skill does had to open the file by path.
    """
    import difflib
    import sys
    from pathlib import Path

    records = discover_skills()
    by_name = {str(record["name"]): record for record in records}
    record = by_name.get(args.name)
    if record is None:
        close = difflib.get_close_matches(args.name, list(by_name), n=1, cutoff=0.6)
        hint = f" Did you mean '{close[0]}'?" if close else ""
        print(f"error: unknown skill '{args.name}'.{hint} See 'aiswmm skill list'.", file=sys.stderr)
        return 2
    path = Path(str(record["path"]))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 1
    lines = text.splitlines()
    print(f"{record['name']} - {path}")
    print()
    shown = lines if args.full else lines[:60]
    print("\n".join(shown))
    if not args.full and len(lines) > 60:
        print(f"\n... ({len(lines) - 60} more lines; pass --full)")
    return 0


def list_skills(args: argparse.Namespace) -> int:
    records = load_skill_registry() if args.registry else discover_skills()
    for record in records:
        status = "enabled" if record.get("enabled", True) else "disabled"
        print(f"{record['name']} ({status}) - {record['path']}")
    if args.registry:
        print(f"registry: {skills_registry_path()}")
    return 0
