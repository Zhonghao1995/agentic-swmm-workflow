from __future__ import annotations

import argparse
import os
import sys

from agentic_swmm import __version__
from agentic_swmm.agent.help_router import (
    GroupedHelpFormatter,
    render_top_level_help,
    route_help_verb,
)
from agentic_swmm.commands import agent, audit, bootstrap_memory, calibrate, capabilities, cite, cite_param, compare, config, demo, doctor, mcp, memory, model, plot, run, setup, skill, storm, transfer, uncertainty
from agentic_swmm.commands.expert import calibration as expert_calibration
from agentic_swmm.commands.expert import gap_promote as expert_gap_promote
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
    # PRD-06 Phase D.4: bootstrap memory scaffold. Top-level so the
    # default-router does not punt it to the agent — the command
    # creates only the on-disk skeleton.
    "bootstrap",
    # PRD-06 Phase B verbs. Registered top-level so the default-router
    # does not punt them to the agent — both are deterministic surfaces
    # over pure functions in agentic_swmm/agent/swmm_runtime/ and
    # agentic_swmm/memory/.
    "compare",
    "cite",
    # PRD-06 §2.2 reverse-lookup of a parameter value to its citation
    # (Round 2). Listed top-level so the default-router does not punt
    # it to the agent — pure CLI surface over a memory verb.
    "cite-param",
    # PRD-06 Phase B.4 verbs. Top-level so the default-router does not
    # punt them to the agent — both are deterministic surfaces over
    # pure functions.
    "storm",
    # PRD-07 Phase 5 verb. ``aiswmm transfer`` recommends warm-start
    # parameters for a fresh INP by ranking calibrated prior cases by
    # watershed similarity. Listed top-level so the default-router does
    # not punt it to the agent — it is a deterministic CLI surface.
    "transfer",
    # Uncertainty integration deliverable (issue #55). Lives at the top
    # level so the default-router does not punt it to the agent — it is
    # a deterministic CLI surface over a pure function.
    "uncertainty",
    # PRD-06 Phase C.5 verb. Checkpoint-aware calibration loop.
    "calibrate",
    # Expert-only commands (PRD-Z). Listed here so the default-router
    # does not punt them to the agent; the agent itself has no
    # ToolSpec entries for these names.
    "calibration",
    "pour_point",
    "thresholds",
    "publish",
    # PRD-GF-PROMOTE: expert-only gap-fill case-level promotion.
    "gap",
    # PRD-CASE-ID: case-level namespace surface. ``case`` covers
    # ``init``/``show``; ``list`` is the top-level lister
    # (``aiswmm list cases``) per the PRD's CLI surface table.
    "case",
    "list",
    # PRD-08 A.2: ``aiswmm help`` routes to verb-level --help via
    # ``help_router.route_help_verb``. Listed top-level so the default
    # router does not punt help requests to the LLM planner.
    "help",
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
    # PRD-08 A.2: use GroupedHelpFormatter so the description (which
    # carries the grouped verb block) is rendered verbatim instead of
    # word-wrapped by argparse's default formatter. The description
    # itself is built lazily — we want the registered set to land in
    # ``subparsers.choices`` before we ask the formatter for the
    # filtered block, but for the top-level help text the union from
    # VERB_GROUPS is sufficient because every grouped verb is also
    # registered below.
    parser = argparse.ArgumentParser(
        prog="agentic-swmm",
        formatter_class=GroupedHelpFormatter,
        description=render_top_level_help(),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # PRD-08 A.2 (audit #7): ``--ignore-memory`` was historically only
    # consumed by the pre-parse strip in :func:`_strip_ignore_memory`,
    # so ``aiswmm --help`` did not document it. Add it to the top-level
    # parser as a real argument (action="store_true") so it shows up in
    # the help block. The strip step still owns the cross-position
    # parsing semantics; this declaration is purely documentation.
    parser.add_argument(
        "--ignore-memory",
        dest="_ignore_memory_documented",
        action="store_true",
        help=(
            "One-shot escape hatch: disable the memory-informed runtime "
            "for this invocation. Works regardless of where on the "
            "command line it appears."
        ),
    )

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
    # PRD-06 Phase D.4 — bootstrap the memory skeleton.
    bootstrap_memory.register(subparsers)
    # PRD-06 Phase B verbs.
    compare.register(subparsers)
    cite.register(subparsers)
    # PRD-06 §2.2 — reverse-lookup parameter -> citation (Round 2).
    cite_param.register(subparsers)
    # PRD-06 Phase B.4 — algorithmic design-storm generator.
    storm.register(subparsers)
    # PRD-07 Phase 5 — cross-watershed transfer-learning surface.
    transfer.register(subparsers)
    # Issue #55 — uncertainty source decomposition (paper-reviewer view).
    uncertainty.register(subparsers)
    # PRD-06 Phase C.5 — checkpoint-aware calibration runner facade.
    calibrate.register(subparsers)
    # Expert-only commands (PRD-Z). Surfaced as top-level subcommands
    # so the help renders an "expert-only" grouping naturally; none of
    # them is registered as an agent ToolSpec or as an MCP tool.
    expert_calibration.register(subparsers)
    expert_pour_point.register(subparsers)
    expert_thresholds.register(subparsers)
    expert_publish.register(subparsers)
    expert_gap_promote.register(subparsers)
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
    # PRD-08 A.2: ``aiswmm help`` subcommand. It receives the rest of
    # the argv as ``help_args`` and forwards to ``aiswmm <verb> --help``
    # via :func:`route_help_verb`. Listed last so help registration
    # cannot perturb the verb registration order.
    _register_help_subcommand(subparsers)
    return parser


def _register_help_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``aiswmm help [<verb> ...]``.

    The subparser takes any trailing tokens verbatim (``nargs="*"``)
    so the router decides whether they name a known verb or warrant a
    "unknown verb" stderr. The ``func`` defaults to
    :func:`_help_main`, which calls
    :func:`agentic_swmm.agent.help_router.route_help_verb` with the
    tokens.
    """
    help_parser = subparsers.add_parser(
        "help",
        help=(
            "Show help for a verb. ``aiswmm help`` prints the top-level "
            "grouped help; ``aiswmm help <verb>`` shows that verb's "
            "--help block."
        ),
    )
    help_parser.add_argument("help_args", nargs="*", help=argparse.SUPPRESS)
    help_parser.set_defaults(func=_help_main)


def _help_main(args: argparse.Namespace) -> int:
    """``aiswmm help`` entry point — forward to the help router."""
    return route_help_verb(list(getattr(args, "help_args", []) or []))


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
    # ``--ignore-memory`` is a top-level escape hatch for the
    # memory-informed runtime. We strip it from argv *before* the
    # default-router prepends ``agent`` and *before* argparse parses,
    # so the flag works regardless of which subcommand the user
    # invokes (``aiswmm --ignore-memory plot ...`` /
    # ``aiswmm plot --ignore-memory ...``). The flag is one-shot:
    # the env var lives only for the duration of this invocation,
    # so chained commands in the same shell session pick the memory
    # back up automatically.
    argv, ignore_memory = _strip_ignore_memory(argv)
    argv = _route_default_to_agent(argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    from agentic_swmm.agent.feature_flags import MEMORY_INFORMED_ENV

    prior_env = os.environ.get(MEMORY_INFORMED_ENV)
    if ignore_memory:
        os.environ[MEMORY_INFORMED_ENV] = "1"
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        # Restore the env so a second call in the same process does
        # not inherit our toggle. The flag is one-shot by contract.
        if ignore_memory:
            if prior_env is None:
                os.environ.pop(MEMORY_INFORMED_ENV, None)
            else:
                os.environ[MEMORY_INFORMED_ENV] = prior_env


def _strip_ignore_memory(argv: list[str]) -> tuple[list[str], bool]:
    """Remove the top-level ``--ignore-memory`` flag from ``argv``.

    Returns the cleaned argv and a boolean indicating whether the
    flag was present. The flag is a bare boolean (no value), so we
    drop it wherever it appears.
    """
    if "--ignore-memory" not in argv:
        return argv, False
    return [arg for arg in argv if arg != "--ignore-memory"], True


def _route_default_to_agent(argv: list[str]) -> list[str]:
    if not argv:
        return ["agent", "--planner", "openai", "--interactive"]
    if argv[0] == "chat":
        return ["agent", "--planner", "openai", *argv[1:]] if len(argv) > 1 else ["agent", "--planner", "openai", "--interactive"]
    if argv[0] in COMMANDS:
        if (
            argv[0] == "run"
            and "--inp" not in argv
            and "--help" not in argv
            and "-h" not in argv
        ):
            # ``aiswmm run`` without ``--inp`` falls through to the
            # natural-language planner so the user can describe the
            # model in prose. ``--help``/``-h`` short-circuit this so
            # ``aiswmm run --help`` actually shows the run subparser's
            # usage.
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
