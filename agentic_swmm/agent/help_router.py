"""Top-level help grouping + ``aiswmm help <verb>`` router (PRD-08 A.2).

The historical top-level ``aiswmm --help`` listed all 28 verbs in a
flat alphabetical block. A first-time user could not tell which verbs
were core (``run``/``audit``/``plot``) from which were memory-store
inspection or expert escape hatches. This module replaces the flat
list with grouped sections and provides a thin router so the typo-
friendly ``aiswmm help <verb>`` lands the user on the verb's own
``--help`` rather than being misrouted to the LLM planner.

Two surfaces:

* :func:`render_top_level_help` returns the grouped block as text.
  ``cli.py`` plugs it into the top-level parser's ``description``.
* :func:`route_help_verb` is the ``aiswmm help`` subcommand. It
  forwards ``aiswmm help <verb>`` to ``aiswmm <verb> --help`` via a
  subprocess so we share one source of truth for verb help.

The verb groupings are an editorial decision; they encode the PRD-08
audit's "what does a new user need first?" ordering.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import OrderedDict

# ---------------------------------------------------------------------
# Editorial groupings
# ---------------------------------------------------------------------
#
# The groups are ordered: most users encounter Core workflow verbs on
# day one, Memory verbs on week one, Expert verbs only when they
# graduate to publication-grade work. ``Inspection`` and ``Setup``
# live at the bottom because they answer "what is broken?" or "where
# does my config live?", which are rare once onboarding is done.
#
# ``VERB_GROUPS`` is the single source of truth. Help rendering reads
# from it, and the integration tests assert each registered verb is
# represented exactly once.


VERB_GROUPS: "OrderedDict[str, list[str]]" = OrderedDict(
    [
        ("Core workflow", ["run", "audit", "plot", "demo"]),
        (
            "Memory",
            [
                "compare",
                "cite",
                "cite-param",
                "storm",
                "transfer",
                "uncertainty",
                "calibrate",
                "bootstrap",
            ],
        ),
        (
            "Expert",
            [
                "pour_point",
                "thresholds",
                "gap",
                "publish",
                "calibration",
            ],
        ),
        ("Inspection", ["doctor", "capabilities", "list", "memory"]),
        ("Case namespace", ["case"]),
        (
            "Setup",
            ["setup", "mcp", "skill", "model", "config", "agent"],
        ),
    ]
)


# One-line descriptions. The text is what shows up next to the verb in
# the grouped help block. Each description is a sentence so screen
# readers and "explain like I'm five" tools render it cleanly.
VERB_DESCRIPTIONS: dict[str, str] = {
    "run": "Execute SWMM on an INP, write audit + plots.",
    "audit": "Re-write audit notes for an existing run.",
    "plot": "Render rain/runoff/depth plots from a run directory.",
    "demo": "Run a packaged demo case end-to-end.",
    "compare": "Diff continuity/peak/runoff between two runs.",
    "cite": "Look up an entry in the citations library by key.",
    "cite-param": "Reverse-lookup a citation by parameter name + value.",
    "storm": "Generate a design hyetograph (uniform/triangular/chicago/huff/scs).",
    "transfer": "Suggest starter parameters for a new case from similar past cases.",
    "uncertainty": "Plan or rebuild uncertainty runs (SALib, Morris/Sobol).",
    "calibrate": "Calibrate parameters against observed data (stub today).",
    "bootstrap": "Scaffold memory stores in a project directory.",
    "doctor": "Diagnose install, memory stores, opt-out knobs.",
    "capabilities": "Print the agent's registered tool capabilities.",
    "list": "List repository-level entities (cases, ...).",
    "memory": "Inspect or migrate memory stores.",
    "case": "Case-level namespace operations (init, show).",
    "setup": "First-run setup wizard (provider, MCP, memory).",
    "mcp": "Manage MCP server registration.",
    "skill": "Inspect bundled skills.",
    "model": "View or change the active LLM provider/model.",
    "config": "View or edit ~/.aiswmm/config.toml.",
    "agent": "Drop into the agent planner (interactive or one-shot).",
    "pour_point": "Expert: pour-point promotion workflow.",
    "thresholds": "Expert: edit memory-derived QA thresholds.",
    "gap": "Expert: gap-fill case-level promotion.",
    "publish": "Expert: publish a case to the case registry.",
    "calibration": "Expert: low-level calibration workspace.",
}


# ---------------------------------------------------------------------
# render_top_level_help
# ---------------------------------------------------------------------


def render_top_level_help(
    *, registered_verbs: list[str] | None = None
) -> str:
    """Return the grouped help block as a single string.

    Arguments:
        registered_verbs: When passed, restrict the output to verbs
            actually present in argparse's ``choices``. ``cli.py``
            uses this so a verb that fails to import does not appear
            in the help. Defaults to every verb in
            :data:`VERB_GROUPS`.

    Layout:
      * a one-line usage hint,
      * each non-empty group as a header + indented rows,
      * a trailing pointer to ``aiswmm help <verb>``.
    """
    filter_set = set(registered_verbs) if registered_verbs is not None else None
    lines: list[str] = []
    lines.append(
        "usage: aiswmm [-h] [--version] [--ignore-memory] <verb> [<args>...]"
    )
    lines.append("")
    for group, verbs in VERB_GROUPS.items():
        present = [v for v in verbs if filter_set is None or v in filter_set]
        if not present:
            continue
        lines.append(f"{group}:")
        for verb in present:
            description = VERB_DESCRIPTIONS.get(verb, "")
            lines.append(f"  {verb:<14}  {description}")
        lines.append("")
    lines.append(
        "For verb-specific help: aiswmm help <verb>  "
        "or  aiswmm <verb> --help"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------
# route_help_verb
# ---------------------------------------------------------------------


def _known_verbs() -> set[str]:
    """Union of every verb declared in :data:`VERB_GROUPS`."""
    return {verb for verbs in VERB_GROUPS.values() for verb in verbs}


def route_help_verb(
    args: list[str], *, runner: callable | None = None
) -> int:
    """Route ``aiswmm help <verb>`` to ``aiswmm <verb> --help``.

    Arguments:
        args: The argument list left after the ``help`` token has been
            consumed. ``[]`` means the user typed plain ``aiswmm help``;
            ``["compare"]`` means ``aiswmm help compare``; ``["foo"]``
            means an unknown verb.
        runner: Test seam. Defaults to a subprocess that invokes the
            installed ``aiswmm`` script via the same Python; tests
            inject a fake to assert routing without spawning processes.

    Exit codes:
      * 0 — top-level or verb help rendered.
      * 2 — unknown verb (stderr says so).
    """
    if not args:
        sys.stdout.write(render_top_level_help() + "\n")
        return 0
    verb = args[0]
    extras = args[1:]
    if verb not in _known_verbs():
        sys.stderr.write(
            f"unknown verb: {verb}; try aiswmm --help for a verb list\n"
        )
        return 2
    if runner is None:
        runner = _default_runner
    return runner([verb, *extras])


def _default_runner(verb_argv: list[str]) -> int:
    """Spawn ``python -m agentic_swmm.cli <verb> --help`` and forward exit code.

    Using the live module name keeps the ``--help`` text in lockstep
    with the verb's own argparse object; we never re-implement the
    help renderer here.
    """
    cmd = [sys.executable, "-m", "agentic_swmm.cli", *verb_argv, "--help"]
    result = subprocess.run(cmd, check=False)
    return result.returncode


# ---------------------------------------------------------------------
# GroupedHelpFormatter — keeps argparse's --help block but with a
# description that already contains the grouped section. We rely on
# the existing argparse formatter for everything else; the description
# is rendered verbatim.
# ---------------------------------------------------------------------


class GroupedHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Reuse argparse's raw-description formatter for the top-level parser.

    The class is a marker today (no behaviour change beyond
    inheriting :class:`argparse.RawDescriptionHelpFormatter`). It
    exists so ``cli.py`` and the tests can refer to a stable type
    when checking that the top-level parser uses our formatter.
    """


__all__ = [
    "GroupedHelpFormatter",
    "VERB_DESCRIPTIONS",
    "VERB_GROUPS",
    "render_top_level_help",
    "route_help_verb",
]
