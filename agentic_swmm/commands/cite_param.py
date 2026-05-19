"""``aiswmm cite-param`` — reverse-lookup a parameter value to its citation.

Forward ``aiswmm cite <key>`` answers "what is citation_key X?". This
subcommand answers the inverse: "given Manning's n = 0.013 for asphalt,
what literature range backs that value?". The reverse lookup walks
``reference_benchmarks.yaml`` for the dotted parameter name, checks
whether the value is inside ``[min, max]``, and prints the matching
citation.

Output:
  * Default — two-line human-readable text:
      <param> = <value> is <IN|OUT OF> range [min, max]
      citation: <authors year — title>
  * ``--json`` — machine-readable payload (``ParameterCitation.to_dict``).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_swmm.agent.flag_naming import (
    register_example_flag,
    register_quiet_flag,
)
from agentic_swmm.memory.citations import cite_parameter_choice
from agentic_swmm.utils.paths import repo_root


_CITE_PARAM_EXAMPLE = (
    "aiswmm cite-param --name manning_n_overland.asphalt --value 0.013 --json"
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "cite-param",
        help=(
            "Reverse-lookup: given a parameter name + value, print the "
            "matching reference range and citation (PRD-06 §2.2)."
        ),
    )
    parser.add_argument(
        "--name",
        required=True,
        help=(
            "Dotted parameter name, e.g. 'manning_n_overland.asphalt'. "
            "Must resolve to a leaf in reference_benchmarks.yaml that "
            "carries numeric min/max and a citation key."
        ),
    )
    parser.add_argument(
        "--value",
        type=float,
        required=True,
        help="Numeric value of the parameter the modeler chose.",
    )
    parser.add_argument(
        "--benchmarks-path",
        type=Path,
        default=None,
        help=(
            "Optional override for reference_benchmarks.yaml. "
            "Defaults to memory/modeling-memory/reference_benchmarks.yaml."
        ),
    )
    parser.add_argument(
        "--citations-path",
        type=Path,
        default=None,
        help=(
            "Optional override for citations.yaml. "
            "Defaults to memory/modeling-memory/citations.yaml."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit ParameterCitation as JSON on stdout.",
    )
    register_quiet_flag(parser)
    register_example_flag(parser, example_text=_CITE_PARAM_EXAMPLE)
    parser.set_defaults(func=main)


def _default_benchmarks_path() -> Path:
    return repo_root() / "memory" / "modeling-memory" / "reference_benchmarks.yaml"


def _default_citations_path() -> Path:
    return repo_root() / "memory" / "modeling-memory" / "citations.yaml"


def main(args: argparse.Namespace) -> int:
    benchmarks_path = args.benchmarks_path or _default_benchmarks_path()
    citations_path = args.citations_path or _default_citations_path()

    result = cite_parameter_choice(
        parameter_name=args.name,
        value=args.value,
        benchmarks_path=benchmarks_path,
        citations_path=citations_path,
    )

    if result is None:
        message = {
            "ok": False,
            "reason": "parameter_or_range_not_found",
            "parameter_name": args.name,
            "value": args.value,
            "benchmarks_path": str(benchmarks_path),
        }
        if getattr(args, "json", False):
            print(json.dumps(message, indent=2, sort_keys=True))
        else:
            print(
                f"parameter '{args.name}' has no resolvable range in "
                f"{benchmarks_path}"
            )
        return 1

    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    in_or_out = "IN" if result.in_range else "OUT OF"
    print(
        f"{result.parameter_name} = {result.value} is {in_or_out} range "
        f"[{result.range_min}, {result.range_max}]"
    )
    if result.citation_full is not None:
        c = result.citation_full
        print(f"citation: {c.authors} {c.year} — {c.title} ({c.work})")
    else:
        print(
            f"citation: '{result.citation_key}' (entry missing from citations.yaml)"
        )
    return 0
