"""``aiswmm capabilities`` — list the agent's registered tools.

The legacy output was a flat alphabetical list of ~35 tool names with
no descriptions. PRD-08 Phase B (audit #39) groups the tools by
category (Build / Run / Audit / Analyze / Memory / Inspect /
MCP-meta) and shows the ``ToolSpec.description`` next to each name so
a user reading the screen learns what each tool actually does.

Tools that don't match any category fall into ``Other`` so the output
is exhaustive — we never silently drop a tool from the listing.
"""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict

from agentic_swmm.agent.flag_naming import (
    register_example_flag,
    register_json_flag,
    register_quiet_flag,
)
from agentic_swmm.agent.policy import capability_summary
from agentic_swmm.agent.tool_registry import AgentToolRegistry


_CAPABILITIES_EXAMPLE = "aiswmm capabilities"


# Editorial groupings for the tool listing (PRD-08 Phase B / audit #39).
# Order matters: ``Build`` is first because that is what a fresh user
# does first. ``Memory`` is last because it is the most niche.
# A tool name may appear in at most one group; tools missing from
# every group land in ``Other``.
CAPABILITY_GROUPS: "OrderedDict[str, tuple[str, ...]]" = OrderedDict(
    [
        (
            "Build",
            (
                "build_inp",
                "format_rainfall",
                "network_qa",
                "network_to_inp",
                "synth_swmm_from_bbox",
            ),
        ),
        (
            "Run",
            (
                "run_swmm_inp",
                "demo_acceptance",
            ),
        ),
        (
            "Audit",
            (
                "audit_run",
                "doctor",
                "capabilities",
            ),
        ),
        (
            "Analyze",
            (
                "plot_run",
                "inspect_plot_options",
            ),
        ),
        (
            "Memory",
            (
                "recall_memory",
                "recall_memory_search",
                "recall_session_history",
                "record_fact",
                "summarize_memory",
            ),
        ),
        (
            "Skill / MCP",
            (
                "list_skills",
                "read_skill",
                "select_skill",
                "list_mcp_servers",
                "list_mcp_tools",
                "call_mcp_tool",
            ),
        ),
        (
            "Workspace",
            (
                "apply_patch",
                "git_diff",
                "list_dir",
                "read_file",
                "run_allowed_command",
                "run_tests",
                "search_files",
                "web_fetch_url",
                "web_search",
            ),
        ),
        (
            "Human-in-the-loop",
            (
                "request_expert_review",
                "request_gap_judgement",
            ),
        ),
    ]
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "capabilities",
        help="Show Agentic SWMM runtime permissions and available agent tools.",
    )
    register_json_flag(
        parser,
        help_text=(
            "Print machine-readable JSON instead of the grouped human-"
            "readable text listing."
        ),
    )
    register_quiet_flag(parser)
    register_example_flag(parser, example_text=_CAPABILITIES_EXAMPLE)
    parser.set_defaults(func=main)


def _grouped_tools(
    registry: AgentToolRegistry,
) -> "OrderedDict[str, list[tuple[str, str]]]":
    """Walk the registry and bucket each tool by group.

    Returns an ordered dict mapping group label to a list of
    ``(name, description)`` pairs. The lists are alphabetically
    ordered within each group for predictable rendering. Tools
    that don't match any declared group land in ``Other``.
    """
    grouped: "OrderedDict[str, list[tuple[str, str]]]" = OrderedDict()
    for group, _ in CAPABILITY_GROUPS.items():
        grouped[group] = []
    other_bucket: list[tuple[str, str]] = []

    name_to_group: dict[str, str] = {}
    for group, names in CAPABILITY_GROUPS.items():
        for name in names:
            name_to_group[name] = group

    for name in registry.sorted_names():
        description = registry.describe(name) or "(no description provided)"
        # Truncate description to a single sentence for the listing —
        # full description is available via ``--json``.
        first_sentence = description.split(".")[0].strip()
        if first_sentence and not first_sentence.endswith("."):
            first_sentence += "."
        short = first_sentence or description
        group = name_to_group.get(name)
        if group is None:
            other_bucket.append((name, short))
        else:
            grouped[group].append((name, short))

    if other_bucket:
        grouped["Other"] = other_bucket

    # Drop empty groups so we don't print bare headers.
    return OrderedDict((g, items) for g, items in grouped.items() if items)


def main(args: argparse.Namespace) -> int:
    registry = AgentToolRegistry()
    summary = capability_summary(registry.sorted_names())
    if getattr(args, "json", False):
        # JSON mode keeps the historical payload shape (tools = flat
        # list) so downstream consumers don't break, AND adds the
        # grouped mapping so new consumers can pick it up.
        grouped = _grouped_tools(registry)
        payload = dict(summary)
        payload["tools_grouped"] = {
            group: [{"name": n, "description": d} for n, d in items]
            for group, items in grouped.items()
        }
        print(json.dumps(payload, indent=2))
        return 0

    quiet = bool(getattr(args, "quiet", False))
    if not quiet:
        print("Agentic SWMM runtime capabilities")
        print(f"- filesystem read: {summary['filesystem']['read']}")
        print(f"- filesystem write: {summary['filesystem']['write']}")
        print(f"- arbitrary shell: {summary['filesystem']['arbitrary_shell']}")
        print(
            f"- external INP import: "
            f"{summary['swmm']['run_external_inp_import']}"
        )
        print(
            f"- web research: {summary['web']['enabled']} "
            f"({', '.join(summary['web']['tools'])})"
        )
        print(f"- MCP tools: {summary['mcp']['enabled']}")
        print("")
    grouped = _grouped_tools(registry)
    print(f"Tools ({len(registry.sorted_names())} registered):")
    for group, items in grouped.items():
        print(f"  {group}:")
        for name, description in items:
            print(f"    {name}: {description}")
    return 0
