"""Top-level help grouping + ``aiswmm help <verb>`` router (PRD-08 A.2).

The historical top-level ``aiswmm --help`` listed all verbs (28 at the
time; 35 as of ADR-0006, ratcheted by tests/test_surface_ratchets.py) in a
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
        ("Core workflow", ["run", "audit", "plot", "map", "demo", "review", "report", "runs"]),
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
        ("Expert", ["expert"]),
        ("Inspection", ["doctor", "capabilities", "list", "memory", "trace"]),
        ("Case namespace", ["case"]),
        (
            "Setup",
            ["login", "setup", "mcp", "skill", "model", "config", "agent"],
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
    "map": "Render the spatial layout (subcatchments + network + outfalls).",
    "demo": "Run a packaged demo case end-to-end.",
    "review": "Check a completed run against a design-review rulebook.",
    "report": "Assemble a Word deliverable from an audited run directory.",
    "runs": "Run-directory housekeeping (tidy: archive stale unaudited runs).",
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
    "trace": "Pretty-print agent_trace.jsonl / memory_trace.jsonl from a run dir.",
    "case": "Case-level namespace operations (init, show).",
    "login": "Store an LLM provider API key (OpenAI by default).",
    "setup": "First-run setup wizard (provider, MCP, memory).",
    "mcp": "Manage MCP server registration.",
    "skill": "Inspect bundled skills.",
    "model": "View or change the active LLM provider/model.",
    "config": "View or edit ~/.aiswmm/config.toml.",
    "agent": "Drop into the agent planner (interactive or one-shot).",
    "expert": (
        "Expert-only namespace: calibration, pour_point, thresholds, "
        "publish, gap (ADR-0006 D3)."
    ),
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
    # PRD-08 Phase B (cross-cutting): point at the memory runtime
    # docs so a user reading top-level --help knows where the
    # substrate contract + opt-out env vars are documented.
    lines.append(
        "Memory runtime docs: docs/memory_runtime.md  "
        "(opt-out flags, four confidence quadrants)"
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


# PRD-08 Phase B (audit #26): argparse's default usage-line formatter
# wraps mid-flag (``--total-iters\nTOTAL_ITERS``) when a long verb's
# usage exceeds 80 cols, which breaks copy-paste. ``WidthSafeFormatter``
# bumps ``max_help_position`` to 30 (so flag metavars get more room in
# the body) and overrides ``_format_usage`` to re-wrap on whitespace
# boundaries between full actions rather than between an option string
# and its metavar.
class WidthSafeFormatter(argparse.HelpFormatter):
    """Argparse formatter that never wraps in the middle of a flag.

    Two behaviours diverge from the stock :class:`argparse.HelpFormatter`:

    1. ``max_help_position`` defaults to 30 (instead of 24). This gives
       longer flags a bigger column to land in before the help text
       starts, so the body section reads more cleanly.
    2. The post-pass over the formatted usage line rewraps only at
       action-boundary whitespace. We detect "action boundaries" by
       walking the usage text and remembering bracket nesting depth —
       a wrap point at depth==0 between two tokens is safe; anywhere
       inside ``[--total-iters TOTAL_ITERS]`` (depth > 0) or between
       ``--total-iters`` and its un-bracketed metavar is not.

    The class is otherwise a stock argparse formatter — it does not
    touch help-text body wrapping or description rendering. Subclasses
    that need raw description rendering (the top-level parser, the
    uncertainty subparsers) should still inherit from
    :class:`argparse.RawDescriptionHelpFormatter` separately.
    """

    def __init__(
        self,
        prog: str,
        indent_increment: int = 2,
        max_help_position: int = 30,
        width: int | None = None,
    ) -> None:
        super().__init__(
            prog,
            indent_increment=indent_increment,
            max_help_position=max_help_position,
            width=width,
        )

    @staticmethod
    def _action_safe_wrap(
        text: str, *, width: int, indent: str
    ) -> str:
        """Wrap ``text`` so no line splits a ``--flag METAVAR`` group.

        The algorithm walks ``text`` once, tracking square-bracket nesting
        depth and the depth-0 distance to the next safe wrap point. A
        wrap point is a whitespace where depth==0 AND the next token
        starts with ``-``, ``[`` (a new optional group), or a positional
        token. That covers every realistic argparse usage line.
        """

        tokens = WidthSafeFormatter._split_action_tokens(text)
        if not tokens:
            return text
        out_lines: list[str] = []
        current = tokens[0]
        for token in tokens[1:]:
            candidate = f"{current} {token}"
            if len(candidate) <= width:
                current = candidate
            else:
                out_lines.append(current)
                current = f"{indent}{token}"
        if current:
            out_lines.append(current)
        return "\n".join(out_lines)

    @staticmethod
    def _split_action_tokens(text: str) -> list[str]:
        """Split ``text`` on whitespace boundaries that are safe to wrap on.

        "Safe" means: bracket nesting depth==0 AND the next non-space
        character starts a new action (``-`` for an option flag,
        ``[`` for a bracketed optional group). We treat any "space
        inside a bracket" as glue, AND we also glue an option's
        metavar (``--total-iters TOTAL_ITERS``) onto its flag — so
        argparse's required-flag formatting (no brackets) does not
        split between the flag and its value.
        """

        tokens: list[str] = []
        depth = 0
        token_start = 0
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == "[":
                depth += 1
                i += 1
                continue
            if ch == "]":
                depth -= 1
                i += 1
                continue
            if ch.isspace() and depth == 0:
                # Peek at the next non-space character. If it is a
                # METAVAR continuation (any token that does not start
                # a new ``--flag``, positional, or ``[``-group), glue
                # it onto the current token. argparse always emits the
                # metavar right after its flag, so this is a robust
                # heuristic.
                j = i
                while j < n and text[j].isspace():
                    j += 1
                if j >= n:
                    break
                next_ch = text[j]
                next_starts_action = next_ch in ("-", "[")
                if not next_starts_action:
                    # Token continuation. Don't split here; advance.
                    i = j
                    continue
                piece = text[token_start:i].strip()
                if piece:
                    tokens.append(piece)
                # Advance over the whitespace run.
                token_start = j
                i = j
                continue
            i += 1
        tail = text[token_start:n].strip()
        if tail:
            tokens.append(tail)
        return tokens

    def _format_usage(self, usage, actions, groups, prefix):
        # Defer to the parent for the raw "usage:" line first; if the
        # result has a single line we are done (no wrap needed).
        raw = super()._format_usage(usage, actions, groups, prefix)
        # The parent's output looks like:
        #   ``usage: <prog> <wrapped actions text>\n``
        # When the wrap was unsafe we replace the action-text segment
        # with our action-safe rewrap.
        lines = raw.splitlines()
        if len(lines) <= 1:
            return raw
        first = lines[0]
        # The first line is ``usage: <prog> <first chunk>``. Subsequent
        # lines are continuation indents added by argparse. We want to
        # re-join them under our own width-safe wrap.
        # Find the column where ``<prog>`` ends — everything after that
        # is what we re-wrap.
        if prefix is None:
            prefix = "usage: "
        # Determine the indent to use on continuation lines: the column
        # immediately after ``<prog>`` (matches argparse's default).
        prog_string = self._format_text("%(prog)s" % {"prog": self._prog}).strip()
        usage_head = prefix + prog_string
        # Reassemble the full action segment by stripping continuation
        # indents and joining on whitespace.
        action_segment_parts: list[str] = []
        if first.startswith(usage_head):
            after_head = first[len(usage_head) :].lstrip()
            if after_head:
                action_segment_parts.append(after_head)
        else:
            # Defensive fallback — argparse formatting edge case.
            return raw
        for cont in lines[1:]:
            action_segment_parts.append(cont.lstrip())
        action_segment = " ".join(part for part in action_segment_parts if part)
        if not action_segment:
            return raw
        # Compute the wrap width and continuation indent.
        continuation_indent = " " * (len(usage_head) + 1)
        wrap_width = max(self._width - len(continuation_indent), 20)
        first_chunk_budget = max(self._width - len(usage_head) - 1, 20)
        # Greedy first-line packing using the same token split.
        tokens = self._split_action_tokens(action_segment)
        if not tokens:
            return raw
        first_line_tokens: list[str] = []
        carry = ""
        for token in tokens:
            candidate = (carry + " " + token).strip() if carry else token
            if len(candidate) <= first_chunk_budget:
                carry = candidate
                first_line_tokens.append(token)
            else:
                break
        consumed = len(first_line_tokens)
        first_line = f"{usage_head} {carry}".rstrip()
        remaining = tokens[consumed:]
        if not remaining:
            return first_line + "\n"
        # Wrap the remainder under the continuation indent.
        remaining_text = " ".join(remaining)
        remaining_wrapped = self._action_safe_wrap(
            remaining_text,
            width=wrap_width,
            indent=continuation_indent,
        )
        # Each non-first line of ``remaining_wrapped`` already has the
        # indent prepended; the first line needs it added manually.
        remaining_lines = remaining_wrapped.splitlines()
        result_lines = [first_line]
        if remaining_lines:
            head = continuation_indent + remaining_lines[0].lstrip()
            result_lines.append(head)
            for line in remaining_lines[1:]:
                # The wrapper already prepended ``indent`` to
                # continuation lines, but defensively normalize so
                # we don't double-indent.
                if line.startswith(continuation_indent):
                    result_lines.append(line)
                else:
                    result_lines.append(continuation_indent + line.lstrip())
        return "\n".join(result_lines) + "\n"


class WidthSafeRawDescriptionFormatter(
    WidthSafeFormatter, argparse.RawDescriptionHelpFormatter
):
    """``WidthSafeFormatter`` + raw description rendering.

    Used by subparsers that ship an ``epilog`` whose newlines must
    survive (uncertainty's Examples block, etc.). The MRO puts
    ``WidthSafeFormatter`` first so the usage-line rewrite wins;
    ``RawDescriptionHelpFormatter`` then controls the description
    + epilog rendering.
    """


__all__ = [
    "GroupedHelpFormatter",
    "VERB_DESCRIPTIONS",
    "VERB_GROUPS",
    "WidthSafeFormatter",
    "WidthSafeRawDescriptionFormatter",
    "render_top_level_help",
    "route_help_verb",
]
