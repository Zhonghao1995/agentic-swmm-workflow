from __future__ import annotations

import argparse
import os
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
    # PRD-CASE-ID: case-level namespace surface. ``case`` covers
    # ``init``/``show``; ``list`` is the top-level lister
    # (``aiswmm list cases``) per the PRD's CLI surface table.
    "case",
    "list",
}


# CONCURRENCY-OWNER: PRD-CASE-ID
def _add_case_id_flag(parser: argparse.ArgumentParser) -> None:
    """Attach the shared ``--case-id <slug>`` flag.

    Every subcommand that produces or consumes a run gets this flag,
    so every PRD-downstream feature can call ``resolve_case_id`` with
    ``declared=args.case_id``. The flag is intentionally optional —
    a missing slug is not a CLI parse error; the resolver decides
    whether to infer, prompt, or fail-loud.
    """
    parser.add_argument(
        "--case-id",
        dest="case_id",
        default=None,
        metavar="SLUG",
        help=(
            "Case identifier (slug). Pattern ^[a-z][a-z0-9-]{1,63}$ "
            "(e.g. 'tod-creek'). Links the run to a case namespace "
            "under cases/<slug>/."
        ),
    )


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
    # CONCURRENCY-OWNER: PRD-CASE-ID
    _register_case_commands(subparsers)
    # CONCURRENCY-OWNER: PRD-CASE-ID — attach --case-id to existing
    # subcommands that produce or consume runs. Done as a post-pass so
    # the owning command modules stay untouched (their parser objects
    # are the ones in ``subparsers.choices``).
    for name in ("agent", "run"):
        sub = subparsers.choices.get(name)
        if sub is not None:
            _add_case_id_flag(sub)
    return parser


# CONCURRENCY-OWNER: PRD-CASE-ID
def _register_case_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``aiswmm list cases``, ``aiswmm case show``, ``aiswmm case init``.

    The PRD calls for three new surfaces. Two of them live under the
    ``case`` namespace (``show`` and ``init``); the third is a
    top-level ``list cases`` so the CLI reads as plain English when
    the user types it. The two namespaces share a single backend
    (``agentic_swmm.case.case_registry``), so the dispatch here is
    thin glue.
    """
    # ``aiswmm list cases``
    list_parser = subparsers.add_parser(
        "list", help="List repository-level entities (cases, ...)."
    )
    list_sub = list_parser.add_subparsers(dest="list_target")
    list_cases_parser = list_sub.add_parser(
        "cases", help="List known cases under cases/<id>/."
    )
    list_cases_parser.set_defaults(func=_list_cases_main)
    # Falling through to ``aiswmm list`` with no target should print a
    # helpful message rather than argparse's default ``error: ...``.
    list_parser.set_defaults(func=_list_main)

    # ``aiswmm case {show,init}``
    case_parser = subparsers.add_parser(
        "case", help="Case-level namespace operations (init, show)."
    )
    case_sub = case_parser.add_subparsers(dest="case_command")

    show = case_sub.add_parser("show", help="Print case_meta.yaml for a case.")
    show.add_argument("case_id", help="Slug of the case to show.")
    show.set_defaults(func=_case_show_main)

    init = case_sub.add_parser(
        "init",
        help=(
            "Initialise cases/<id>/case_meta.yaml. In headless mode "
            "(AISWMM_HEADLESS=1) requires --display-name; otherwise "
            "prompts interactively."
        ),
    )
    init.add_argument("case_id", help="Slug for the new case.")
    init.add_argument("--display-name", help="Human-readable case name.")
    init.add_argument("--study-purpose", help="One-line purpose of the case.")
    init.add_argument(
        "--area-km2",
        type=float,
        default=None,
        help="Catchment area in km^2 (optional).",
    )
    init.add_argument("--land-use", default=None, help="Catchment land-use (optional).")
    init.add_argument(
        "--region-descriptor",
        default=None,
        help="Region descriptor, e.g. 'Pacific Northwest, BC, Canada' (optional).",
    )
    init.set_defaults(func=_case_init_main)

    case_parser.set_defaults(func=_case_main)


# CONCURRENCY-OWNER: PRD-CASE-ID
def _list_main(args: argparse.Namespace) -> int:
    target = getattr(args, "list_target", None)
    if target is None:
        print(
            "usage: aiswmm list <target>\n  targets: cases",
            file=sys.stderr,
        )
        return 2
    return 0


# CONCURRENCY-OWNER: PRD-CASE-ID
def _list_cases_main(args: argparse.Namespace) -> int:
    from agentic_swmm.case import case_registry

    metas = case_registry.list_cases(case_registry.repo_root())
    if not metas:
        print("no cases found under cases/")
        return 0
    print(f"{len(metas)} case(s):")
    for meta in metas:
        label = meta.display_name or "(no display name)"
        print(f"  {meta.case_id}  {label}")
    return 0


# CONCURRENCY-OWNER: PRD-CASE-ID
def _case_main(args: argparse.Namespace) -> int:
    cmd = getattr(args, "case_command", None)
    if cmd is None:
        print(
            "usage: aiswmm case {init,show} <case_id>",
            file=sys.stderr,
        )
        return 2
    return 0


# CONCURRENCY-OWNER: PRD-CASE-ID
def _case_show_main(args: argparse.Namespace) -> int:
    from agentic_swmm.case import case_registry
    from agentic_swmm.case.case_id import CaseIdValidationError

    try:
        meta = case_registry.read_case_meta(
            args.case_id, repo_root=case_registry.repo_root()
        )
    except (case_registry.CaseMetaNotFoundError, CaseIdValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    def _fmt(value: object) -> str:
        # Render Python ``None`` as YAML's ``null`` so the show output
        # is paste-able back into a case_meta.yaml file.
        if value is None:
            return "null"
        return str(value)

    print(f"case_id: {meta.case_id}")
    print(f"display_name: {meta.display_name}")
    print(f"study_purpose: {meta.study_purpose}")
    print(f"created_utc: {meta.created_utc}")
    print("catchment:")
    for key, value in (meta.catchment or {}).items():
        print(f"  {key}: {_fmt(value)}")
    print("inputs:")
    for key, value in (meta.inputs or {}).items():
        print(f"  {key}: {_fmt(value)}")
    if meta.notes:
        print("notes: |")
        for line in meta.notes.splitlines():
            print(f"  {line}")
    return 0


# CONCURRENCY-OWNER: PRD-CASE-ID
def _case_init_main(args: argparse.Namespace) -> int:
    from agentic_swmm.case import case_registry
    from agentic_swmm.case.case_id import (
        CaseIdValidationError,
        validate_case_id,
    )

    try:
        validate_case_id(args.case_id)
    except CaseIdValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    headless = os.environ.get("AISWMM_HEADLESS") == "1" or not sys.stdin.isatty()

    display_name = args.display_name
    study_purpose = args.study_purpose or ""
    area_km2 = args.area_km2
    land_use = args.land_use
    region_descriptor = args.region_descriptor

    if not display_name:
        if headless:
            print(
                "error: --display-name is required in non-interactive mode",
                file=sys.stderr,
            )
            return 1
        display_name = input("display_name> ").strip()
        if not study_purpose:
            study_purpose = input("study_purpose> ").strip()

    meta = case_registry.CaseMeta(
        case_id=args.case_id,
        display_name=display_name or args.case_id,
        study_purpose=study_purpose,
        created_utc="",
        catchment={
            "area_km2": area_km2,
            "land_use": land_use,
            "region_descriptor": region_descriptor,
        },
        inputs={"dem": None, "observed_flow": None},
        notes="",
    )
    try:
        path = case_registry.write_case_meta(
            meta, repo_root=case_registry.repo_root()
        )
    except case_registry.CaseMetaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


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
