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

PRD-08 A.3 (audit #13 & #40): lookup failures used to print a bare
"no resolvable range" line; we now differentiate four failure modes
(unknown parameter, leaf null, missing citation key, citation
unregistered) and emit a structured cause/hint stanza. Fuzzy
suggestions are surfaced on parameter-name typos.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agentic_swmm.agent.error_remediation import (
    fuzzy_match_suggestions,
    parameter_lookup_error,
)
from agentic_swmm.agent.flag_naming import (
    register_example_flag,
    register_quiet_flag,
)
from agentic_swmm.memory.citations import cite_parameter_choice
from agentic_swmm.memory.reference_benchmarks import (
    load_reference_benchmarks,
)
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


def _collect_dotted_keys(node: Any, prefix: str = "") -> list[str]:
    """Walk a benchmarks dict and return every dotted leaf path.

    A leaf is any dict that does NOT have a sub-dict child — those are
    the parameter entries (with ``min`` / ``max`` / ``citation`` keys).
    Used to build the candidate pool for the fuzzy-match suggestion.
    """
    out: list[str] = []
    if not isinstance(node, dict):
        return out
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            # Heuristic: a leaf entry holds ``min``/``max``/``citation``;
            # an intermediate node holds further dicts. We emit the path
            # only when the value is a *leaf* (no nested dict child) so
            # the suggestion list isn't polluted with category names.
            child_dicts = [v for v in value.values() if isinstance(v, dict)]
            if not child_dicts:
                out.append(path)
            else:
                out.extend(_collect_dotted_keys(value, prefix=path))
    return out


def _classify_failure(
    *, parameter_name: str, benchmarks_path: Path, citations_path: Path
) -> tuple[str, str | None]:
    """Return ``(failure_mode, citation_key)`` for a None ``cite_parameter_choice``.

    The forward lookup returned ``None``; we walk the benchmarks again
    to discover which of the four modes applies so the user gets a
    targeted cause/hint.
    """
    name = str(parameter_name).strip()
    if not name:
        return ("unknown_parameter", None)
    data = load_reference_benchmarks(benchmarks_path)
    cursor: Any = data
    for part in name.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return ("unknown_parameter", None)
        cursor = cursor[part]
    if not isinstance(cursor, dict):
        return ("unknown_parameter", None)
    raw_min = cursor.get("min")
    raw_max = cursor.get("max")
    if raw_min is None or raw_max is None:
        return ("leaf_uncurated", None)
    citation_key = cursor.get("citation")
    if not citation_key or not str(citation_key).strip():
        return ("missing_citation_key", None)
    # The leaf has a citation key but the lookup returned None — the
    # forward function only returns None for unknown/leaf/citation
    # cases so we won't actually reach here from
    # ``cite_parameter_choice``, but keep the branch for safety.
    return ("missing_citation_key", str(citation_key))


def _emit_lookup_error(args: argparse.Namespace, err: Any) -> int:
    """Render ``err`` either as JSON (stdout) or stderr stanza."""
    if getattr(args, "json", False):
        payload = {
            "ok": False,
            "reason": "parameter_or_range_not_found",
            "parameter_name": args.name,
            "value": args.value,
            "benchmarks_path": str(args.benchmarks_path or _default_benchmarks_path()),
            "summary": err.summary,
            "cause": err.cause,
            "hint": err.hint,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stderr.write(err.format_for_stderr() + "\n")
    return 1


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
        failure_mode, citation_key = _classify_failure(
            parameter_name=args.name,
            benchmarks_path=benchmarks_path,
            citations_path=citations_path,
        )
        similar: list[str] = []
        if failure_mode == "unknown_parameter":
            all_keys = _collect_dotted_keys(load_reference_benchmarks(benchmarks_path))
            similar = fuzzy_match_suggestions(query=args.name, choices=all_keys)
        err = parameter_lookup_error(
            parameter_name=args.name,
            benchmarks_path=benchmarks_path,
            citations_path=citations_path,
            similar_names=similar,
            failure_mode=failure_mode,
            citation_key=citation_key,
        )
        return _emit_lookup_error(args, err)

    # Result populated but the citation key may still be unregistered;
    # surface that as a distinct failure mode if the user is in JSON
    # mode (text mode keeps the friendly "entry missing" line below).
    if result.citation_full is None and getattr(args, "json", False) is False:
        # Continue to the text rendering — keep parity with the
        # pre-A.3 behaviour: print the range + the unregistered-key
        # marker on a single block. (JSON callers still get
        # citation_full=null in the payload.)
        pass

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
        # PRD-08 A.3 (audit #13): when the leaf names a citation key
        # that has no library entry, surface the structured cause/hint
        # so the user knows where to add the citation.
        err = parameter_lookup_error(
            parameter_name=args.name,
            benchmarks_path=benchmarks_path,
            citations_path=citations_path,
            failure_mode="citation_unregistered",
            citation_key=result.citation_key,
        )
        sys.stderr.write(err.format_for_stderr() + "\n")
    return 0
