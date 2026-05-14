"""``aiswmm agent`` CLI: ``--safe`` flag selects SAFE, default is QUICK.

PRD (Default-QUICK profile + visible banner): the agent subcommand
exposes ``--safe`` (no argument) that opts in to the prompting-for-
everything SAFE profile. Without it, the agent runs under QUICK and
auto-approves the read-only catalogue. The legacy ``--quick`` flag
stays as a hidden alias for one release (covered in
``test_agent_cli_quick_alias_warns.py``).
"""
from __future__ import annotations

import unittest

from agentic_swmm.cli import build_parser
from agentic_swmm.commands.agent import resolve_profile_string


class AgentCliSafeFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_default_resolves_to_quick(self) -> None:
        args = self.parser.parse_args(["agent"])
        self.assertEqual(resolve_profile_string(args), "quick")

    def test_safe_flag_resolves_to_safe(self) -> None:
        args = self.parser.parse_args(["agent", "--safe"])
        self.assertEqual(resolve_profile_string(args), "safe")

    def test_safe_flag_help_mentions_default_quick(self) -> None:
        # Help text is the user's primary documentation surface — it
        # must call out both that --safe means "prompt for every tool
        # call" and that the default is QUICK.
        help_text = self.parser.format_help()
        # The agent subcommand help isn't included verbatim in the
        # top-level help, so probe the subparser action directly.
        agent_subparser = self._agent_subparser(self.parser)
        agent_help = agent_subparser.format_help()
        self.assertIn("--safe", agent_help)
        lowered = agent_help.lower()
        self.assertIn("safe", lowered)
        self.assertIn("quick", lowered)

    @staticmethod
    def _agent_subparser(parser):
        for action in parser._actions:  # noqa: SLF001 - argparse public via convention
            if hasattr(action, "choices") and action.choices and "agent" in action.choices:
                return action.choices["agent"]
        raise AssertionError("agent subparser not found")


if __name__ == "__main__":
    unittest.main()
