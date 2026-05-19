"""``aiswmm cite`` — print a citation entry (PRD-06 Phase B.2).

Surfaces ``memory/modeling-memory/citations.yaml`` to the CLI so a
human (or an agent running in transparency mode) can audit which work
backs a parameter choice. ``aiswmm cite <key>`` is the thin verb.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_swmm.agent.flag_naming import (
    register_example_flag,
    register_quiet_flag,
)
from agentic_swmm.memory.citations import recall_citation
from agentic_swmm.utils.paths import repo_root


_CITE_EXAMPLE = "aiswmm cite huber_dickinson_1988"


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "cite",
        help="Print a citation entry from citations.yaml (PRD-06 B.2).",
    )
    parser.add_argument(
        "citation_key",
        help="Citation token, e.g. 'huber_dickinson_1988_t4_5'.",
    )
    parser.add_argument(
        "--citations-path",
        type=Path,
        default=None,
        help=(
            "Optional override for the citations.yaml location. "
            "Defaults to memory/modeling-memory/citations.yaml."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the citation entry as JSON on stdout instead of plain text.",
    )
    register_quiet_flag(parser)
    register_example_flag(parser, example_text=_CITE_EXAMPLE)
    parser.set_defaults(func=main)


def _default_path() -> Path:
    return repo_root() / "memory" / "modeling-memory" / "citations.yaml"


def main(args: argparse.Namespace) -> int:
    path = args.citations_path or _default_path()
    entry = recall_citation(path, args.citation_key)
    if entry is None:
        message = {
            "ok": False,
            "reason": "citation_not_found",
            "citation_key": args.citation_key,
            "citations_path": str(path),
        }
        if getattr(args, "json", False):
            print(json.dumps(message, indent=2, sort_keys=True))
        else:
            print(
                f"citation '{args.citation_key}' not found in {path}"
            )
        return 1
    if getattr(args, "json", False):
        print(json.dumps(entry.to_dict(), indent=2, sort_keys=True))
        return 0
    print(f"key: {entry.key}")
    print(f"authors: {entry.authors}")
    print(f"year: {entry.year}")
    print(f"title: {entry.title}")
    print(f"work: {entry.work}")
    print(f"locator: {entry.locator}")
    if entry.url:
        print(f"url: {entry.url}")
    print(
        "verified: "
        + ("yes" if entry.is_verified else "no")
        + (f" (by {entry.verified_by} on {entry.verified_on})" if entry.is_verified else "")
    )
    return 0
