"""Pin the ``--max-steps`` default at the agent / chat CLI surface.

The original default of 16 was too tight for the LLM-driven dispatch
era: gpt-5.5 routinely burns ~15 steps on read-only introspection
(``list_skills`` / ``read_skill`` ×N / ``list_mcp_servers`` /
``list_mcp_tools`` ×N / ``select_skill`` ×N) before the first real
modeling op, leaving 0-1 step for the actual chain. Concretely:

* 2026-05-27 NYC midtown e2e: 28 tool calls, only 4 real ops, planner
  hit ``max_steps=16`` mid-second-audit and never wrote a final
  natural-language answer.
* 2026-05-27 Greenwich e2e: 32 tool calls, planner cut off BEFORE
  ``plot_run`` could render the conduit hydrograph the prompt asked
  for.

Both surfaces (interactive ``aiswmm chat`` and one-shot ``aiswmm
agent``) now default to 40, leaving ~25 steps for real operations
after the introspection overhead. This test locks the bump in so a
silent revert to 16 fails CI loudly.

The argument's help text also has to keep mentioning when to bump
or lower the value — that's the user's first stop when their run
hits ``max_steps`` again on a denser prompt.
"""

from __future__ import annotations

import unittest

from agentic_swmm.cli import build_parser


_EXPECTED_DEFAULT = 40
# The 16 sentinel is the OLD default. We don't ban the literal string
# entirely (the help text may legitimately reference it as the "tight
# budget" example), but we DO ban it from coming back as the default
# integer value.


class MaxStepsDefaultPinTests(unittest.TestCase):
    """Argparse default-value lock-in."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_aiswmm_agent_default_max_steps_is_40(self) -> None:
        args = self.parser.parse_args(["agent"])
        self.assertEqual(
            args.max_steps,
            _EXPECTED_DEFAULT,
            f"`aiswmm agent` default --max-steps regressed to "
            f"{args.max_steps}; the LLM-driven dispatch era needs ~40 "
            f"because gpt-5.5 burns ~15 steps on introspection before "
            f"the first real op. See 2026-05-27 Greenwich e2e for the "
            f"failure mode.",
        )

    # The stand-by commands/chat.py module was deleted by ADR-0006 D3;
    # ``chat`` is a pure router alias to ``agent --planner llm`` (rewrite
    # pinned in test_agentic_swmm_cli.py), so the agent default asserted
    # above IS the chat default by construction.

    def test_max_steps_can_still_be_overridden(self) -> None:
        """Sanity: bumping the default didn't break the override path."""
        args = self.parser.parse_args(["agent", "--max-steps", "8"])
        self.assertEqual(args.max_steps, 8)

    def test_help_text_explains_when_to_bump(self) -> None:
        """The help text is where users learn why the default is 40 and
        when to bump it. Locking on key phrases keeps the explainer
        intact across future help-rewrites."""
        from agentic_swmm.commands import agent as agent_cmd

        # Build the subparser directly so we can read its formatted help.
        import argparse

        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers()
        agent_cmd.register(sub)
        agent_help = sub.choices["agent"].format_help().lower()
        self.assertIn("introspection", agent_help)
        self.assertIn("--max-steps", agent_help)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
