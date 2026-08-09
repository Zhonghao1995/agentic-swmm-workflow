"""Regression test: single-shot sessions must not end with a question.

Bug (2026-08-09 NL sweep, B1): the base planner prompt tells the model
to "ask the user to choose" for ambiguous plot options and to "stop and
ask" for missing inputs. In a single-shot session the user cannot reply
to the final message, so sessions ended with unanswerable questions
(observed live twice: the plot flow asked node/attr, the canada flow
asked for a bbox, both after the session was over).

Fix under test: ``run_openai_planner`` prepends a ``<session-mode>``
extra to the system prompt when the session is NOT interactive,
instructing the planner to take documented recommended defaults and to
phrase any hard stop as a rerun instruction rather than a question.
Interactive turns get no such block: there, asking is answerable and
stays the preferred behavior.
"""

from __future__ import annotations

import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent import runtime_loop


def _args(interactive: bool) -> Namespace:
    return Namespace(
        interactive=interactive,
        planner="llm",
        provider=None,
        model=None,
        max_steps=4,
        verbose=False,
        dry_run=True,
        safe=False,
        goal=["x"],
        session_id=None,
        session_dir=None,
    )


class SingleShotPromptModeTests(unittest.TestCase):
    def _captured_extras(self, interactive: bool) -> list[str]:
        captured: dict[str, list[str]] = {}

        def fake_plan(**kwargs):
            captured["extras"] = list(kwargs.get("system_prompt_extras") or [])
            return mock.Mock(ok=True, final_text="", plan=[], results=[])

        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            session_dir.mkdir()
            with mock.patch.object(runtime_loop, "run_openai_plan", fake_plan):
                with mock.patch.object(
                    runtime_loop, "write_session_state", create=True
                ) as _:
                    try:
                        runtime_loop.run_openai_planner(
                            _args(interactive),
                            "test goal",
                            session_dir,
                            session_dir / "agent_trace.jsonl",
                            registry=mock.Mock(
                                names=["doctor"], sorted_names=lambda: ["doctor"]
                            ),
                        )
                    except Exception:
                        # Downstream bookkeeping may fail on mocks; the
                        # extras were captured before run_openai_plan
                        # returned, which is all this test needs.
                        pass
        return captured.get("extras", [])

    def test_single_shot_gets_session_mode_block(self) -> None:
        extras = self._captured_extras(interactive=False)
        mode_blocks = [e for e in extras if e.startswith("<session-mode>")]
        self.assertEqual(len(mode_blocks), 1)
        self.assertIn("never end with a question", mode_blocks[0])
        self.assertIn("recommended default", mode_blocks[0])

    def test_interactive_gets_no_session_mode_block(self) -> None:
        extras = self._captured_extras(interactive=True)
        self.assertFalse(
            [e for e in extras if e.startswith("<session-mode>")]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
