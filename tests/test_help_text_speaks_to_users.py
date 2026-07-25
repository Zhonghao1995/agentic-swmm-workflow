"""Help text is a user surface, not a changelog.

Two failures this pins:

* `calibrate` was described as a "stub today" long after ADR-0005 shipped
  the real SCE-UA engine in v0.7.6, so the product told users its newest
  capability was unfinished;
* `--help` cited PRD, ADR and issue numbers. Users cannot look any of
  those up, because that layer is deliberately kept private. The
  traceability belongs in comments, where it already lives.
"""
from __future__ import annotations

import argparse
import re

import pytest

from agentic_swmm.agent.help_router import VERB_DESCRIPTIONS
from agentic_swmm.cli import build_parser


# PRD-06 B.1, ADR-0006 D3, issue #55, PRD-06 §2.2 and similar.
INTERNAL_REFERENCE = re.compile(
    r"PRD[-\s]?\d|ADR[-\s]?\d|issue\s*#\d|§", re.IGNORECASE
)


def _user_visible_help_strings() -> list[tuple[str, str]]:
    """Every help string a user can reach from ``aiswmm --help``.

    Three distinct sources, all of which print:

    * the curated grouped block (``VERB_DESCRIPTIONS``);
    * the one-liner passed to ``add_parser(help=...)``, which argparse
      keeps on the *parent* as a pseudo-action, not on the subparser;
    * each verb's own flags and description.
    """
    strings = [
        (f"description of {verb}", text) for verb, text in VERB_DESCRIPTIONS.items()
    ]

    def walk(parser, prefix: str) -> None:
        for action in parser._actions:
            if action.help and action.help is not argparse.SUPPRESS:
                strings.append((f"{prefix}--{action.dest}".strip(), action.help))
            for pseudo in getattr(action, "_choices_actions", []):
                if pseudo.help:
                    strings.append((f"{prefix}{pseudo.dest} summary", pseudo.help))
            choices = getattr(action, "choices", None) or {}
            if not isinstance(choices, dict):
                continue
            for verb, subparser in choices.items():
                if subparser.description:
                    strings.append((f"{prefix}{verb} description", subparser.description))
                walk(subparser, f"{prefix}{verb} ")

    walk(build_parser(), "")
    return strings


def test_no_help_string_cites_a_document_the_user_cannot_read():
    leaks = [
        f"{where}: {text!r}"
        for where, text in _user_visible_help_strings()
        if INTERNAL_REFERENCE.search(text)
    ]
    assert leaks == [], (
        "internal document references reached the user-facing help:\n"
        + "\n".join(leaks)
    )


def test_calibrate_is_not_described_as_unfinished():
    """ADR-0005 shipped the real engine in v0.7.6."""
    description = VERB_DESCRIPTIONS["calibrate"].lower()

    assert "stub" not in description
    assert "observed" in description


def test_the_two_help_surfaces_agree_about_calibrate():
    """The grouped block and argparse must not describe it differently."""
    parser = build_parser()
    argparse_help = ""
    for action in parser._actions:
        choices = getattr(action, "choices", None) or {}
        if isinstance(choices, dict) and "calibrate" in choices:
            argparse_help = (choices["calibrate"].description or "") + str(
                getattr(action, "_choices_actions", "")
            )
    combined = (VERB_DESCRIPTIONS["calibrate"] + argparse_help).lower()

    assert "stub" not in combined


@pytest.mark.parametrize(
    "sample",
    ["Compare two runs (PRD-06 B.1).", "folded under expert (ADR-0006 D3)", "issue #55"],
)
def test_the_guard_actually_catches_leaks(sample):
    """A guard that cannot fail protects nothing."""
    assert INTERNAL_REFERENCE.search(sample)
