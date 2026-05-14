"""``--quick`` is a hidden alias for backward compatibility.

For one release we keep ``--quick`` parsable so existing scripts /
docs don't break, but:

1. ``--quick`` is hidden from ``--help`` (SUPPRESS).
2. When both ``--quick`` and ``--safe`` are passed, ``--safe`` wins
   and a single-line warning is emitted to stderr.
3. ``--quick`` alone still resolves to QUICK (now also the implicit
   default, so this is a no-op but kept for symmetry).
"""
from __future__ import annotations

import contextlib
import io
import unittest

from agentic_swmm.cli import build_parser
from agentic_swmm.commands.agent import resolve_profile_string


class AgentCliQuickAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_quick_alone_resolves_to_quick(self) -> None:
        args = self.parser.parse_args(["agent", "--quick"])
        self.assertEqual(resolve_profile_string(args), "quick")

    def test_quick_alias_is_hidden_from_help(self) -> None:
        agent_help = self._agent_subparser(self.parser).format_help()
        # --quick must still parse but must not appear in --help output.
        self.assertNotIn("--quick", agent_help)
        self.assertIn("--safe", agent_help)

    def test_safe_wins_when_both_flags_passed_and_warns(self) -> None:
        args = self.parser.parse_args(["agent", "--quick", "--safe"])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            profile_string = resolve_profile_string(args)
        self.assertEqual(profile_string, "safe")
        warning = stderr.getvalue()
        self.assertTrue(
            warning.strip(),
            "expected a single-line warning to stderr when --quick and --safe both passed",
        )
        lowered = warning.lower()
        self.assertIn("--safe", lowered)
        self.assertIn("--quick", lowered)

    def test_no_warning_when_only_safe_passed(self) -> None:
        args = self.parser.parse_args(["agent", "--safe"])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            profile_string = resolve_profile_string(args)
        self.assertEqual(profile_string, "safe")
        self.assertEqual(stderr.getvalue(), "")

    def test_no_warning_when_only_quick_passed(self) -> None:
        args = self.parser.parse_args(["agent", "--quick"])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            profile_string = resolve_profile_string(args)
        self.assertEqual(profile_string, "quick")
        self.assertEqual(stderr.getvalue(), "")

    @staticmethod
    def _agent_subparser(parser):
        for action in parser._actions:  # noqa: SLF001 - argparse public via convention
            if hasattr(action, "choices") and action.choices and "agent" in action.choices:
                return action.choices["agent"]
        raise AssertionError("agent subparser not found")


if __name__ == "__main__":
    unittest.main()
