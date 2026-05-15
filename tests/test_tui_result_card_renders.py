"""PRD-TUI-REDESIGN: final result card renders with all six fields.

``reporting.render_result_card`` produces the rounded-frame
``[SYS] RUN COMPLETE`` block. The card has six labelled fields
(outcome / run_dir / metrics / artifacts / boundary / next) so the
user sees a structured summary at the end of every workflow.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from agentic_swmm.agent import reporting
from agentic_swmm.agent import tui_chrome


class _TTYStream:
    def isatty(self) -> bool:
        return True


class ResultCardRendersTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["AISWMM_TUI"] = "retro"
        os.environ.pop("NO_COLOR", None)
        self._stdout_patch = mock.patch.object(tui_chrome._sys, "stdout", _TTYStream())
        self._stdout_patch.start()

    def tearDown(self) -> None:
        self._stdout_patch.stop()

    @staticmethod
    def _strip_ansi(text: str) -> str:
        import re

        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    def test_result_card_contains_all_six_fields(self) -> None:
        card = reporting.render_result_card(
            outcome="SUCCESS",
            run_dir=Path("runs/2026-05-14/183245_tecnopolo_run"),
            metrics="continuity 0.02% err",
            artifacts="3 artifact(s)",
            boundary="ran + audited, not calibrated",
            next_action="aiswmm plot --run-dir <above>",
        )
        plain = self._strip_ansi(card)
        for label in ("Outcome:", "Run dir:", "Metrics:", "Artifacts:", "Boundary:", "Next:"):
            self.assertIn(label, plain)
        self.assertIn("SUCCESS", plain)
        self.assertIn("tecnopolo", plain)
        self.assertIn("RUN COMPLETE", plain)

    def test_result_card_uses_rounded_frame(self) -> None:
        card = reporting.render_result_card(
            outcome="SUCCESS",
            run_dir="runs/x",
            metrics="m",
            artifacts="a",
            boundary="b",
            next_action="n",
        )
        plain = self._strip_ansi(card)
        for ch in ("╭", "╮", "╰", "╯", "│", "─"):
            self.assertIn(ch, plain)

    def test_result_card_from_run_classifies_success(self) -> None:
        card = reporting.render_result_card_from_run(
            session_dir=Path("runs/x"),
            results=[{"ok": True, "tool": "build_inp"}, {"ok": True, "tool": "run_swmm_inp"}],
            dry_run=False,
        )
        self.assertIn("SUCCESS", self._strip_ansi(card))

    def test_result_card_from_run_classifies_failure(self) -> None:
        card = reporting.render_result_card_from_run(
            session_dir=Path("runs/x"),
            results=[{"ok": True, "tool": "build_inp"}, {"ok": False, "tool": "run_swmm_inp"}],
            dry_run=False,
        )
        self.assertIn("FAIL", self._strip_ansi(card))

    def test_plain_mode_drops_frame_characters(self) -> None:
        os.environ["AISWMM_TUI"] = "plain"
        card = reporting.render_result_card(
            outcome="SUCCESS",
            run_dir="runs/x",
            metrics="m",
            artifacts="a",
            boundary="b",
            next_action="n",
        )
        for ch in ("╭", "╮", "╰", "╯", "│", "─"):
            self.assertNotIn(ch, card)
        # All six fields still readable.
        for label in ("Outcome:", "Run dir:", "Metrics:", "Artifacts:", "Boundary:", "Next:"):
            self.assertIn(label, card)


if __name__ == "__main__":
    unittest.main()
