"""The Y/N approval prompt must own its terminal line and its input.

User live test 2026-08-09, downtown Victoria chain: the executor's
RUNNING spinner ticker repainted the line head every ~120 ms while
``input()`` waited, stranding the cursor mid-text
("…— 10stch_swmm_from_canada? [Y/n]"); and a control character the
user pressed during the planner's Thinking wait sat in the tty line
buffer and was consumed as the answer, recording "N (skipped)"
against an explicit Y and failing the whole turn.

Contract pinned here: ``request_approval`` prepares the prompt line
(pause + wipe the live spinner, flush pending tty input) BEFORE
reading, and restores the spinner AFTER — on every exit path,
including decline and EOF.
"""
from __future__ import annotations

import io
import unittest
from unittest import mock

from agentic_swmm.agent import permissions, ui


class _Tty(io.StringIO):
    def isatty(self) -> bool:  # noqa: D102 - stdin stand-in
        return True

    def fileno(self) -> int:  # noqa: D102 - stdin stand-in
        return 99


class RequestApprovalPromptSeamTests(unittest.TestCase):
    """Order of the prepare/read/restore seam on every exit path."""

    def _run(self, answer: str = "y", *, raise_eof: bool = False):
        calls: list[str] = []

        def fake_input(prompt: str) -> str:
            calls.append("input")
            if raise_eof:
                raise EOFError
            return answer

        with mock.patch.dict(
            # tests/conftest.py auto-approves suite-wide so agent tests
            # never block on a prompt; these tests ARE about the prompt.
            permissions.os.environ,
            {permissions.AUTO_APPROVE_ENV: "0"},
        ), mock.patch.object(permissions.sys, "stdin", _Tty()), mock.patch.object(
            permissions,
            "_prepare_prompt_line",
            side_effect=lambda: calls.append("prepare"),
        ), mock.patch.object(
            permissions,
            "_restore_after_prompt",
            side_effect=lambda: calls.append("restore"),
        ), mock.patch(
            "builtins.input", side_effect=fake_input
        ):
            decision = permissions.request_approval("fetch_swmm_from_canada")
        return decision, calls

    def test_granted_path_prepares_before_input_and_restores_after(self) -> None:
        decision, calls = self._run("y")
        self.assertEqual(calls, ["prepare", "input", "restore"])
        self.assertTrue(decision.approved)
        self.assertEqual(decision.reason, "granted")

    def test_declined_path_still_restores(self) -> None:
        decision, calls = self._run("n")
        self.assertEqual(calls, ["prepare", "input", "restore"])
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "declined")

    def test_eof_path_still_restores(self) -> None:
        decision, calls = self._run(raise_eof=True)
        self.assertEqual(calls, ["prepare", "input", "restore"])
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "headless")


class PreparePromptLineTests(unittest.TestCase):
    """_prepare_prompt_line pauses the spinner and drops buffered input."""

    def test_pauses_spinner_and_flushes_stdin(self) -> None:
        with mock.patch.object(permissions.sys, "stdin", _Tty()), mock.patch.object(
            ui, "pause_active_spinner_for_prompt"
        ) as pause, mock.patch("termios.tcflush") as tcflush:
            permissions._prepare_prompt_line()
        pause.assert_called_once_with()
        tcflush.assert_called_once()
        self.assertEqual(tcflush.call_args.args[0], 99)

    def test_restore_resumes_spinner(self) -> None:
        with mock.patch.object(ui, "resume_active_spinner_after_prompt") as resume:
            permissions._restore_after_prompt()
        resume.assert_called_once_with()

    def test_flush_failure_never_blocks_the_prompt(self) -> None:
        with mock.patch.object(permissions.sys, "stdin", _Tty()), mock.patch(
            "termios.tcflush", side_effect=OSError("bad fd")
        ):
            permissions._prepare_prompt_line()  # must not raise


class UiSpinnerPromptHelpersTests(unittest.TestCase):
    """The ui-level helpers act on the registered spinner and tolerate none."""

    def tearDown(self) -> None:
        ui.set_active_tool_spinner(None)

    def test_pause_helper_pauses_and_wipes_the_registered_spinner(self) -> None:
        spinner = mock.Mock()
        spinner._is_tty = True
        spinner._closed = False
        ui.set_active_tool_spinner(spinner)
        ui.pause_active_spinner_for_prompt()
        spinner.pause.assert_called_once_with()
        spinner.stream.write.assert_called_once_with(ui.ui_colors.CLEAR_LINE)

    def test_resume_helper_resumes_the_registered_spinner(self) -> None:
        spinner = mock.Mock()
        ui.set_active_tool_spinner(spinner)
        ui.resume_active_spinner_after_prompt()
        spinner.resume.assert_called_once_with()

    def test_helpers_are_noops_without_a_spinner(self) -> None:
        ui.set_active_tool_spinner(None)
        ui.pause_active_spinner_for_prompt()
        ui.resume_active_spinner_after_prompt()


if __name__ == "__main__":
    unittest.main()
