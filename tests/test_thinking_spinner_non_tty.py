"""Non-TTY fallback for the thinking spinner (issue #58, UX-3).

When ``stdout.isatty()`` is False (CI logs, captured pipes, redirected
files) the thinking spinner must NOT animate — it should emit one
plain newline-terminated line on entry and another on exit, just like
the tool spinner from PR #35. This keeps CI logs grep-able.
"""
from __future__ import annotations

import io
import unittest

from agentic_swmm.agent.ui import Spinner, SpinnerState


class ThinkingSpinnerNonTTYTests(unittest.TestCase):
    def test_thinking_spinner_no_animation_on_non_tty(self) -> None:
        # Plain StringIO — its ``isatty()`` returns False by default.
        stream = io.StringIO()
        with Spinner("Thinking…", stream=stream, state=SpinnerState.THINKING):
            # No update calls — represents a single LLM "wait" window.
            pass
        output = stream.getvalue()
        # 1) No carriage-return animation framing.
        self.assertNotIn(
            "\r",
            output,
            "non-TTY thinking spinner must not use \\r framing",
        )
        # 2) The label was emitted at least once so CI logs show
        # what the agent was doing during the wait.
        self.assertIn("Thinking", output)

    def test_thinking_spinner_emits_single_line_on_entry_non_tty(self) -> None:
        stream = io.StringIO()
        with Spinner("Thinking…", stream=stream, state=SpinnerState.THINKING):
            pass
        output = stream.getvalue()
        # A no-update spinner on non-TTY should produce exactly one
        # newline-terminated line. (Compare with the tool spinner
        # which emits one line per update; we have zero updates here.)
        self.assertEqual(
            output.count("\n"),
            1,
            f"non-TTY thinking spinner must emit one line; got {output!r}",
        )


if __name__ == "__main__":
    unittest.main()
