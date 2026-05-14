"""Task-shaped first messages must skip the warm intro (issue #59).

When the user's first message contains a task verb (``run`` / ``build``
/ ``跑`` / ``做`` / ``plot`` / ``calibrate`` / ``audit`` / ``check`` /
``test``), the planner must dispatch directly without the warm intro.
The user already told us what they want to do; making them wait for an
intro would feel patronising.
"""

from __future__ import annotations

import os
import re
import unittest
from unittest import mock

from agentic_swmm.agent.runtime_loop import (
    is_open_shaped_prompt,
    maybe_warm_intro,
)


_INTRO_RE = re.compile(r"I'?m Agentic SWMM", flags=re.IGNORECASE)


class TaskPromptClassifierTests(unittest.TestCase):
    """``is_open_shaped_prompt`` must return False for task-shaped prompts."""

    def test_chinese_run_verb(self) -> None:
        self.assertFalse(is_open_shaped_prompt("跑 tecnopolo demo"))
        self.assertFalse(is_open_shaped_prompt("帮我做一个 SWMM model"))

    def test_english_run_verb(self) -> None:
        for prompt in (
            "run examples/tecnopolo/tecnopolo_r1_199401.inp",
            "Run the tecnopolo demo for me",
            "run audit on runs/tecnopolo",
        ):
            with self.subTest(prompt=prompt):
                self.assertFalse(is_open_shaped_prompt(prompt))

    def test_english_build_verb(self) -> None:
        self.assertFalse(is_open_shaped_prompt("build inp from subcatchments.csv"))
        self.assertFalse(is_open_shaped_prompt("Build a SWMM model for Tod Creek"))

    def test_plot_calibrate_audit_check_test_verbs(self) -> None:
        for prompt in (
            "plot total_inflow at node J2",
            "calibrate against observed flow",
            "audit runs/tecnopolo",
            "check continuity",
            "test the prepared INP",
        ):
            with self.subTest(prompt=prompt):
                self.assertFalse(is_open_shaped_prompt(prompt))


class MaybeWarmIntroSkipsTaskPrompts(unittest.TestCase):
    """``maybe_warm_intro`` returns None for task-shaped first messages."""

    def test_first_turn_task_prompt_skips_intro(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AISWMM_DISABLE_WELCOME", None)
            self.assertIsNone(maybe_warm_intro("跑 tecnopolo demo", turn=1))
            self.assertIsNone(
                maybe_warm_intro(
                    "run examples/tecnopolo/tecnopolo_r1_199401.inp", turn=1
                )
            )
            self.assertIsNone(
                maybe_warm_intro("build inp from subcatchments.csv", turn=1)
            )
            self.assertIsNone(maybe_warm_intro("run audit on runs/foo", turn=1))

    def test_task_prompt_does_not_emit_intro_text(self) -> None:
        """Belt-and-suspenders: if anything *does* come back, it must
        not be the intro template (regression guard)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AISWMM_DISABLE_WELCOME", None)
            for prompt in (
                "跑 tecnopolo demo",
                "build inp",
                "run audit",
                "plot the rainfall at J2",
                "calibrate this model against observed flow",
            ):
                with self.subTest(prompt=prompt):
                    text = maybe_warm_intro(prompt, turn=1)
                    if text is not None:
                        # Should never happen — but if classifier
                        # regresses, fail loud.
                        self.assertNotRegex(text, _INTRO_RE)


if __name__ == "__main__":
    unittest.main()
