"""Tests for the ``SpinnerState`` enum.

Issue #58 (UX-3): the spinner gains a ``SpinnerState`` enum so it can
be reused for non-tool work (LLM "thinking" phase, future "waiting on
user" prompts, terminal Done/Failed states).  This test pins the
public surface — five states, all importable from
``agentic_swmm.agent.ui``.
"""
from __future__ import annotations

import unittest

from agentic_swmm.agent.ui import SpinnerState


class SpinnerStateEnumTests(unittest.TestCase):
    def test_spinner_state_has_five_named_states(self) -> None:
        names = {state.name for state in SpinnerState}
        self.assertEqual(
            names,
            {"THINKING", "RUNNING", "WAITING", "DONE", "FAILED"},
            "SpinnerState must expose exactly THINKING/RUNNING/WAITING/DONE/FAILED",
        )

    def test_each_state_importable_by_name(self) -> None:
        # All five are reachable via attribute access — this is the
        # form callers will typically use (``SpinnerState.THINKING``).
        for attr in ("THINKING", "RUNNING", "WAITING", "DONE", "FAILED"):
            self.assertTrue(
                hasattr(SpinnerState, attr),
                f"SpinnerState.{attr} must be defined",
            )


if __name__ == "__main__":
    unittest.main()
