"""Tests for :mod:`agentic_swmm.agent.help_router` (PRD-08 A.2).

The module groups verbs in the top-level help and routes
``aiswmm help <verb>`` to ``aiswmm <verb> --help`` (instead of the LLM
planner). Tests assert that:

1. ``render_top_level_help`` includes every group header and every
   registered verb.
2. ``route_help_verb`` returns exit 0 for known verbs and exit 2 with
   a stderr message for unknown verbs.
3. ``route_help_verb([])`` prints the top-level help.
4. The grouping covers every verb the live CLI registers (catches the
   "added a verb, forgot to group it" footgun).
"""

from __future__ import annotations

import io
import unittest
from unittest import mock

from agentic_swmm.agent.help_router import (
    VERB_DESCRIPTIONS,
    VERB_GROUPS,
    render_top_level_help,
    route_help_verb,
)


class RenderTopLevelHelpTests(unittest.TestCase):
    def test_contains_group_headers(self) -> None:
        text = render_top_level_help()
        self.assertIn("Core workflow:", text)
        self.assertIn("Memory:", text)
        self.assertIn("Expert:", text)
        self.assertIn("Inspection:", text)
        self.assertIn("Setup:", text)

    def test_lists_every_grouped_verb(self) -> None:
        text = render_top_level_help()
        # Every verb in VERB_GROUPS must appear at least once.
        for verbs in VERB_GROUPS.values():
            for verb in verbs:
                self.assertIn(
                    verb,
                    text,
                    msg=f"verb {verb!r} missing from top-level help",
                )

    def test_contains_help_pointer(self) -> None:
        text = render_top_level_help()
        self.assertIn("aiswmm help", text)
        self.assertIn("--help", text)

    def test_filter_to_registered_verbs(self) -> None:
        # Filtering hides verbs that are not yet registered (smoke test
        # for the cli.py wiring path).
        text = render_top_level_help(registered_verbs=["run", "audit"])
        self.assertIn("run", text)
        self.assertIn("audit", text)
        self.assertNotIn("calibrate", text)
        self.assertNotIn("transfer", text)

    def test_descriptions_cover_grouped_verbs(self) -> None:
        # Every verb in a group must have a one-line description, so
        # the grouped help never shows blanks.
        for verbs in VERB_GROUPS.values():
            for verb in verbs:
                self.assertIn(
                    verb,
                    VERB_DESCRIPTIONS,
                    msg=f"verb {verb!r} has no VERB_DESCRIPTIONS entry",
                )
                self.assertTrue(
                    VERB_DESCRIPTIONS[verb].strip(),
                    msg=f"verb {verb!r} has an empty description",
                )


class RouteHelpVerbTests(unittest.TestCase):
    def test_no_args_prints_top_level(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = route_help_verb([])
        self.assertEqual(rc, 0)
        self.assertIn("Core workflow:", out.getvalue())

    def test_known_verb_calls_runner(self) -> None:
        captured = {}

        def fake_runner(verb_argv):
            captured["argv"] = verb_argv
            return 0

        rc = route_help_verb(["compare"], runner=fake_runner)
        self.assertEqual(rc, 0)
        self.assertEqual(captured["argv"], ["compare"])

    def test_known_verb_forwards_trailing_tokens(self) -> None:
        captured = {}

        def fake_runner(verb_argv):
            captured["argv"] = verb_argv
            return 0

        # ``aiswmm help uncertainty plan`` should route to
        # ``aiswmm uncertainty plan --help``.
        rc = route_help_verb(["uncertainty", "plan"], runner=fake_runner)
        self.assertEqual(rc, 0)
        self.assertEqual(captured["argv"], ["uncertainty", "plan"])

    def test_unknown_verb_returns_2_with_stderr(self) -> None:
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            rc = route_help_verb(["frobnicate"])
        self.assertEqual(rc, 2)
        self.assertIn("unknown verb", err.getvalue())
        self.assertIn("frobnicate", err.getvalue())


class GroupCoverageTests(unittest.TestCase):
    """Live registered verbs must each map to exactly one group."""

    def test_every_registered_verb_is_grouped(self) -> None:
        from agentic_swmm.cli import build_parser

        parser = build_parser()
        registered: set[str] = set()
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            if choices:
                registered.update(choices.keys())
        # The top-level ``help`` verb is added in cli.py wiring and is
        # not part of VERB_GROUPS; remove it from the comparison.
        registered.discard("help")
        grouped = {v for verbs in VERB_GROUPS.values() for v in verbs}
        missing = registered - grouped
        self.assertFalse(
            missing,
            msg=f"these registered verbs have no group: {sorted(missing)}",
        )

    def test_no_verb_in_more_than_one_group(self) -> None:
        seen: dict[str, str] = {}
        for group, verbs in VERB_GROUPS.items():
            for verb in verbs:
                if verb in seen:
                    self.fail(
                        f"verb {verb!r} appears in both "
                        f"{seen[verb]!r} and {group!r}"
                    )
                seen[verb] = group


if __name__ == "__main__":
    unittest.main()
