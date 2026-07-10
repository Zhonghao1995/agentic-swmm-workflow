from __future__ import annotations

import argparse
import difflib
import functools
import os
import sys

from agentic_swmm import __version__
from agentic_swmm.memory.run_progress import (
    list_partial_state_files as _list_partial_state_files,
)
from agentic_swmm.agent.help_router import (
    GroupedHelpFormatter,
    render_top_level_help,
    route_help_verb,
)
from agentic_swmm.commands import agent, audit, bootstrap_memory, calibrate, capabilities, cite, cite_param, compare, config, demo, doctor, login, map as map_cmd, mcp, memory, model, plot, report, review, run, runs_tidy, setup, skill, storm, trace, transfer, uncertainty
from agentic_swmm.commands.expert import calibration as expert_calibration
from agentic_swmm.commands.expert import gap_promote as expert_gap_promote
from agentic_swmm.commands.expert import pour_point as expert_pour_point
from agentic_swmm.commands.expert import publish as expert_publish
from agentic_swmm.commands.expert import thresholds as expert_thresholds


def registered_commands() -> frozenset[str]:
    """Every registered CLI verb, derived from ``build_parser()``.

    Single source of truth for the router and the did-you-mean
    rejector. The old hand-maintained ``COMMANDS`` set was a second
    registration site ~250 lines from ``build_parser()``: a verb added
    there but missed here silently routed to the LLM planner instead of
    its own parser. Every per-verb comment in that set said the same
    thing — "listed top-level so the default-router does not punt it to
    the agent" — which is now a structural property of registering the
    verb at all, not a per-verb decision.
    """
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return frozenset(action.choices)
    return frozenset()


registered_commands = functools.lru_cache(maxsize=1)(registered_commands)


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
    login.register(subparsers)
    mcp.register(subparsers)
    skill.register(subparsers)
    doctor.register(subparsers)
    run.register(subparsers)
    audit.register(subparsers)
    plot.register(subparsers)
    # PRD swmmanywhere_integration: spatial-layout counterpart to ``plot``.
    map_cmd.register(subparsers)
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
    # PRD-08 Phase B (#31) — trace pretty-printer for a run directory.
    trace.register(subparsers)
    # ADR-0004 follow-up: runs-directory housekeeping (archive, never delete).
    runs_tidy.register(subparsers)
    # Design-review / code-compliance checker (PRD_design_review.md).
    review.register(subparsers)
    # Client-deliverable Word report (PRD_report_export.md).
    report.register(subparsers)
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
    from agentic_swmm.agent.flag_naming import register_example_flag

    # ``aiswmm list cases``
    list_parser = subparsers.add_parser(
        "list", help="List repository-level entities (cases, ...)."
    )
    register_example_flag(list_parser, example_text="aiswmm list cases")
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
    register_example_flag(case_parser, example_text="aiswmm case show <case-id>")
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
        # PRD-08 Phase B (audit #41): the legacy 2-line message did not
        # tell the user that ``--help`` shows the same content, and there
        # was no banner to surface the list verb's role in the wider CLI.
        # Print a fuller banner here so a user who typed ``aiswmm list``
        # by mistake learns how to discover targets.
        print(
            "usage: aiswmm list <target>\n"
            "\n"
            "List repository-level entities (cases, ...).\n"
            "\n"
            "Targets:\n"
            "  cases     List known cases under cases/<id>/.\n"
            "\n"
            "Run ``aiswmm list --help`` for argparse-style flag help.",
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
    from agentic_swmm.agent.error_remediation import case_not_found
    from agentic_swmm.case import case_registry
    from agentic_swmm.case.case_id import CaseIdValidationError

    try:
        meta = case_registry.read_case_meta(
            args.case_id, repo_root=case_registry.repo_root()
        )
    except case_registry.CaseMetaNotFoundError:
        # PRD-08 A.3 (audit #18): the bare "no case_meta.yaml" line
        # gave no fuzzy hint. Walk the registry to find close-match
        # candidates so a typo like ``tod-creek`` surfaces ``todcreek``.
        try:
            existing = case_registry.list_cases(case_registry.repo_root())
            candidates = [m.case_id for m in existing]
        except Exception:
            candidates = []
        err = case_not_found(slug=args.case_id, candidates=candidates)
        sys.stderr.write(err.format_for_stderr() + "\n")
        return 1
    except CaseIdValidationError as exc:
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
    # Onboarding hole: a bare unknown verb like ``aiswmm bogus`` or
    # ``aiswmm runn`` (typo of ``run``) historically fell through to
    # the LLM planner, so a first-time user without an API key saw
    # ``OPENAI_API_KEY is not set`` and concluded the tool requires a
    # key. Reject single-token unknown verbs up-front with a
    # ``did-you-mean`` hint and the argparse-standard exit code 2.
    # Natural-language goals (``aiswmm inspect the project`` —
    # multiple tokens or a quoted goal containing whitespace) still
    # route to the agent below.
    reject_code = _reject_unknown_verb(argv)
    if reject_code is not None:
        return reject_code
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
        # PRD-08 Phase B (audit #42): when SIGINT lands during a long
        # calibration we historically dropped a bare ``Interrupted.``
        # line and exited. The user had no idea whether progress had
        # been checkpointed, where to look, or how to resume. Scan the
        # ``--run-dir`` (if any) for partial state files and surface a
        # resume hint when we find one.
        run_dir = _resolve_run_dir(args)
        partial = _list_partial_state_files(run_dir)
        if partial:
            print("Interrupted.", file=sys.stderr)
            print("Partial state saved to:", file=sys.stderr)
            for entry in partial:
                print(f"  - {entry}", file=sys.stderr)
            run_id = getattr(args, "run_id", None)
            if run_id:
                print(
                    f"Resume with: aiswmm calibrate --run-id {run_id} ... "
                    "(same args; checkpoint will be picked up)",
                    file=sys.stderr,
                )
        else:
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


def _resolve_run_dir(args: argparse.Namespace) -> "Path | None":
    """Best-effort extraction of the ``--run-dir`` argument from ``args``.

    Returns the resolved Path when the user passed ``--run-dir`` (or
    ``--run_dir``, the argparse-normalised attribute name). Returns
    ``None`` when there is no run-dir on the current command.
    """
    from pathlib import Path

    value = getattr(args, "run_dir", None)
    if value is None:
        return None
    try:
        return Path(value)
    except (TypeError, ValueError):
        return None


def _preflight_interactive_dispatch(argv: list[str]) -> list[str]:
    """Swap the planner to ``rule`` when no LLM provider is configured.

    The interactive shell currently hard-routes to
    ``--planner llm``; if no provider is configured at all, the first
    prompt fails mid-turn. We surface a guidance block on stderr and
    rewrite the dispatched argv to use the rule planner so the user can
    still discover the deterministic verbs without a configured provider.

    Only ``--interactive`` dispatches trigger the preflight — a bare
    one-shot ``agent`` call that the user typed deliberately is left
    alone so the existing error path continues to surface.
    """
    if "--interactive" not in argv:
        return argv
    from agentic_swmm.agent.provider_preflight import check_interactive_provider

    result = check_interactive_provider()
    # Surface any guidance (a soft "no API key" warning when the
    # selected provider has no detectable key, or the full no-provider
    # block) before dispatch.
    if result.guidance_message:
        print(result.guidance_message, file=sys.stderr)
    # A usable provider was selected — keep the LLM planner; the provider
    # authenticates at call time.
    if result.has_configured_provider:
        return argv
    # No usable provider: downgrade to the rule planner so the user still
    # sees the deterministic verbs.
    rewritten: list[str] = []
    swap_next = False
    for item in argv:
        if swap_next:
            rewritten.append(result.fallback_planner)
            swap_next = False
            continue
        if item == "--planner":
            rewritten.append(item)
            swap_next = True
            continue
        rewritten.append(item)
    return rewritten


def _strip_ignore_memory(argv: list[str]) -> tuple[list[str], bool]:
    """Remove the top-level ``--ignore-memory`` flag from ``argv``.

    Returns the cleaned argv and a boolean indicating whether the
    flag was present. The flag is a bare boolean (no value), so we
    drop it wherever it appears.
    """
    if "--ignore-memory" not in argv:
        return argv, False
    return [arg for arg in argv if arg != "--ignore-memory"], True


def _reject_unknown_verb(argv: list[str]) -> int | None:
    """Reject single-token unknown verbs with a ``did-you-mean`` hint.

    Returns the exit code (2, matching argparse's convention) when
    ``argv`` looks like a typo of a known verb; returns ``None`` to
    fall through to the normal router. The rule fires only when:

    * argv has exactly one positional token (the typo);
    * that token does not start with ``-`` (so flags like ``--help``
      still reach argparse);
    * the token contains no whitespace (a quoted multi-word goal such
      as ``aiswmm "inspect the project"`` is still a natural-language
      request);
    * the token is pure ASCII — every registered verb is ASCII, so a
      token with any non-ASCII character (``aiswmm 看看项目里有什么``)
      is a natural-language goal, not a typo candidate;
    * the token is not a known verb, nor the legacy ``chat`` alias.

    A multi-token argv such as ``aiswmm inspect the project`` is a
    real natural-language goal and keeps routing to the agent.
    """
    if len(argv) != 1:
        return None
    token = argv[0]
    if not token or token.startswith("-"):
        return None
    if any(ch.isspace() for ch in token):
        # Quoted natural-language goal; not a verb candidate.
        return None
    if not token.isascii():
        # Registered verbs are all ASCII; non-ASCII text (e.g. a Chinese
        # goal, which often contains no spaces) is a natural-language
        # request and must reach the LLM router, not the typo rejector.
        return None
    if token in registered_commands() or token == "chat":
        return None
    # Build the set of suggestable verbs from the registered parser
    # verbs plus the legacy ``chat`` alias the router still understands.
    candidates = sorted(registered_commands() | {"chat"})
    matches = difflib.get_close_matches(token, candidates, n=1, cutoff=0.6)
    if matches:
        hint = f" Did you mean '{matches[0]}'?"
    else:
        hint = ""
    sys.stderr.write(
        f"error: unknown command '{token}'.{hint} "
        "See 'aiswmm --help' for available commands.\n"
        "To send a free-form goal to the agent, use: "
        f"aiswmm chat \"{token}\"\n"
    )
    return 2


def _route_default_to_agent(argv: list[str]) -> list[str]:
    if not argv:
        # PRD-08 A.3 (audit #6): when the user types bare ``aiswmm`` we
        # are about to drop them into the interactive shell with the
        # LLM planner. If no provider is configured, print a stderr
        # guidance block and downgrade to the rule planner so the user
        # at least sees the deterministic verbs.
        return _preflight_interactive_dispatch(
            ["agent", "--planner", "llm", "--interactive"]
        )
    if argv[0] == "chat":
        dispatched = (
            ["agent", "--planner", "llm", *argv[1:]]
            if len(argv) > 1
            else ["agent", "--planner", "llm", "--interactive"]
        )
        return _preflight_interactive_dispatch(dispatched)
    if argv[0] in registered_commands():
        if (
            argv[0] == "run"
            and "--inp" not in argv
            and "--help" not in argv
            and "-h" not in argv
            # PRD-08 Phase B: ``--example`` is a help-shaped flag that
            # short-circuits to a printed invocation and exits 0; it
            # should never be routed to the LLM planner.
            and "--example" not in argv
        ):
            # ``aiswmm run`` without ``--inp`` falls through to the
            # natural-language planner so the user can describe the
            # model in prose. ``--help``/``-h``/``--example`` short-
            # circuit this so each lands in the run subparser.
            return ["agent", "--planner", "llm", *argv]
        return argv
    if argv[0] in {"-h", "--help", "--version"}:
        return argv
    if argv[0].startswith("-"):
        if _agent_options_without_goal(argv):
            return ["agent", "--planner", "llm", "--interactive", *argv]
        return ["agent", "--planner", "llm", *argv]
    return ["agent", "--planner", "llm", *argv]


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
