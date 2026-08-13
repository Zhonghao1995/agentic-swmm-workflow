"""The goal string does two jobs, and only one of them is for a person.

A continued turn carries the previous message's tail so the planner keeps its
thread. That block was being shown to the user in three places at once: the
terminal echo on every turn, the run's README, and the report header.

    aiswmm> Goal: 增加；同时可增加：

            [Continuation of the previous turn in this session. Your previous
            message ended with:]
            rpt` 中的完整节点流量表，也不会解析 `model.out` 时序**。
            2. `08_plot` 文件夹为空，...

Ten lines of internal plumbing under the one line saying what the turn was,
opening mid-word from a truncated tail.
"""
from __future__ import annotations

import unittest

from agentic_swmm.agent.reporting import display_goal

CONTINUED = """For Victoria, use the verified downtown demo AOI `[-123.370, 48.425]`.

[Continuation of the previous turn in this session. Your previous message ended with:]
dit: `09_audit\\experiment_note.md`
- Provenance: `09_audit\\experiment_provenance.json`
Previous run directory: C:\\Users\\Hoz\\AppData\\Local\\agentic-swmm-workflow\\runs"""


class DisplayGoalTests(unittest.TestCase):
    def test_a_continued_turn_shows_only_what_was_typed(self) -> None:
        self.assertEqual(
            display_goal(CONTINUED),
            "For Victoria, use the verified downtown demo AOI `[-123.370, 48.425]`.",
        )

    def test_the_run_directory_line_goes_too(self) -> None:
        # Its own branch: a continued run appends it without the continuation
        # block, so it has to be cut on its own.
        goal = "run the model\n\nPrevious run directory: C:\\Users\\Hoz\\runs\\x"
        self.assertEqual(display_goal(goal), "run the model")

    def test_an_ordinary_goal_is_untouched(self) -> None:
        for goal in ("run tecnopolo", "画一张降雨径流图", "a goal\nwith a real newline"):
            self.assertEqual(display_goal(goal), goal.strip())

    def test_empty_and_none_are_safe(self) -> None:
        self.assertEqual(display_goal(""), "")
        self.assertEqual(display_goal(None), "")  # type: ignore[arg-type]

    def test_the_planner_still_receives_the_whole_thing(self) -> None:
        # The trim is a display concern. Nothing here may mutate the input,
        # because the string handed to the planner is the same object.
        original = CONTINUED
        display_goal(original)
        self.assertEqual(original, CONTINUED)


class EveryDisplaySurfaceUsesItTests(unittest.TestCase):
    """Three surfaces showed it; all three must go through the helper."""

    def _source(self, module: str) -> str:
        from pathlib import Path

        return Path(module.replace(".", "/") + ".py").read_text(encoding="utf-8")

    def test_the_terminal_echo(self) -> None:
        for module in ("agentic_swmm.agent.runtime_loop", "agentic_swmm.agent.single_shot"):
            source = self._source(module)
            self.assertIn('f"Goal: {display_goal(goal)}"', source, module)
            self.assertNotIn('f"Goal: {goal}"', source, module)

    def test_the_report_header_and_run_readme(self) -> None:
        source = self._source("agentic_swmm.agent.reporting")
        self.assertIn("- goal: {display_goal(goal)}", source)
        self.assertIn("goal=display_goal(goal)", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
