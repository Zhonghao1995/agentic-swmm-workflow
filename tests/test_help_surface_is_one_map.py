"""`aiswmm --help` should be one map of the tool, not three.

It ran 116 lines and listed all 30 verbs three separate times: argparse's
usage line, the curated grouped block, then argparse's `positional
arguments:` list with longer text. That is the first screen a new user
sees.

The program name disagreed with itself too: `prog="agentic-swmm"`
surfaced in usage and in every argparse error, while the installed
command, the docs, and the curated block all say `aiswmm`.
"""
from __future__ import annotations

import io
import re
import contextlib

import pytest

from agentic_swmm.agent.help_router import VERB_GROUPS
from agentic_swmm.cli import build_parser, registered_commands


def _help_text() -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        build_parser().print_help()
    return buffer.getvalue()


def _catalogue_entries(text: str) -> list[str]:
    """Verbs listed as an entry, ignoring mentions inside descriptions.

    An entry is an indented line whose first token is the verb, which is
    the shape the grouped block uses. `run` appearing inside "Re-write
    audit notes for an existing run" is prose, not a second listing.
    """
    return re.findall(r"^  ([a-z][a-z-]*) {2,}\S", text, flags=re.MULTILINE)


def test_the_verb_catalogue_appears_exactly_once():
    """Three catalogues of the same 30 verbs is what made this a wall."""
    entries = _catalogue_entries(_help_text())
    duplicates = sorted({verb for verb in entries if entries.count(verb) > 1})

    assert duplicates == [], f"listed more than once in --help: {duplicates}"


def test_argparse_does_not_dump_the_choices_brace_block():
    """`{agent,model,config,...}` is unreadable and appeared twice."""
    text = _help_text()
    dumps = re.findall(r"\{[a-z-]+,[a-z-]+,[^}]*\}", text)

    assert dumps == [], f"raw choices dump still in --help: {dumps}"


def test_help_fits_on_a_screen():
    """A first screen the user scrolls past teaches nothing."""
    assert len(_help_text().splitlines()) < 60


def test_the_program_calls_itself_what_the_user_types():
    parser = build_parser()

    assert parser.prog == "aiswmm"
    # The repository is named agentic-swmm-workflow and its URL is
    # legitimately in the footer, so check the invocation lines rather
    # than the whole page.
    invocations = [
        line
        for line in _help_text().splitlines()
        if line.startswith("usage:") or " aiswmm " in line
    ]
    assert invocations, "no usage line found"
    assert not any("agentic-swmm " in line for line in invocations)


def test_argparse_errors_also_say_aiswmm(capsys):
    """The prog name reaches usage errors, not just --help."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["report"])

    assert "agentic-swmm" not in capsys.readouterr().err


def test_every_registered_verb_appears_in_the_grouped_block():
    """A verb added to the parser but not grouped would vanish from help."""
    grouped = {verb for verbs in VERB_GROUPS.values() for verb in verbs}
    # ``help`` is the router's own verb and is documented by the footer
    # pointer rather than as a grouped entry.
    missing = registered_commands() - grouped - {"help"}

    assert missing == set(), f"registered but absent from --help groups: {missing}"


def test_no_verb_is_grouped_that_does_not_exist():
    grouped = {verb for verbs in VERB_GROUPS.values() for verb in verbs}
    stale = grouped - registered_commands()

    assert stale == set(), f"grouped but not registered: {stale}"


@pytest.mark.parametrize("verb", ["storm", "uncertainty", "calibrate"])
def test_analysis_verbs_are_not_filed_under_memory(verb):
    """These generate or fit; none of them reads a memory store."""
    assert verb not in VERB_GROUPS["Memory"]


def test_the_footer_points_somewhere_a_pip_user_can_reach():
    """A repo-relative path is meaningless from an installed wheel."""
    doc_lines = [
        line for line in _help_text().splitlines() if "memory_runtime.md" in line
    ]

    assert doc_lines, "the memory runtime pointer disappeared"
    assert all("http" in line for line in doc_lines), doc_lines
