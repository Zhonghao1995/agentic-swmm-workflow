"""REPL loop tests (PRD-02).

Before this PRD, the interactive shell had no testable seam: it called
``input()`` against the real stdin and ``run_openai_planner`` against a
live OpenAI provider. ``run_repl`` extracts the loop with
constructor-injected collaborators so the loop can be exercised with a
canned input queue and a recording planner.

The tests below intentionally do NOT cover banner formatting or session
bootstrap layout — those live in their own modules. The REPL's
responsibility is: read prompt → command dispatch / warm intro / planner
invocation → continue or exit.
"""

from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agentic_swmm.agent.repl import run_repl
from agentic_swmm.agent.warm_intro import WarmIntroState


def _stub_args(**overrides: Any) -> argparse.Namespace:
    """Minimal args namespace — enough for the REPL to dispatch."""
    base = {
        "planner": "openai",
        "session_dir": None,
        "provider": None,
        "model": None,
        "verbose": False,
        "dry_run": False,
        "max_steps": 4,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class _RecordingPlanner:
    """Captures every call so tests can assert what fired."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.return_value = 0

    def __call__(
        self,
        args: argparse.Namespace,
        goal: str,
        session_dir: Path,
        trace_path: Path,
        registry: Any,
        *,
        chat_session: bool = False,
        prior_session_state: dict[str, Any] | None = None,
    ) -> int:
        self.calls.append(
            {
                "goal": goal,
                "session_dir": session_dir,
                "chat_session": chat_session,
                "prior_session_state": prior_session_state,
            }
        )
        return self.return_value


class _QueueInput:
    """Replays a queued list of prompts. ``""`` raises EOFError."""

    def __init__(self, prompts: list[str]) -> None:
        self._prompts = list(prompts)
        self.reads = 0

    def __call__(self, _: str = "") -> str:
        if not self._prompts:
            raise EOFError()
        self.reads += 1
        return self._prompts.pop(0)


class _OutputSink:
    """Captures everything the REPL emits via _agent_say."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, text: str) -> None:
        self.lines.append(text)


class EOFExitsCleanlyTests(unittest.TestCase):
    """An EOF from the input source returns exit code 0."""

    def test_eof_with_no_prompts_returns_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            args = _stub_args()
            planner = _RecordingPlanner()
            rc = run_repl(
                args,
                base_dir=base,
                profile_name="quick",
                input_source=_QueueInput([]),
                planner_runner=planner,
                output=_OutputSink(),
            )
        self.assertEqual(rc, 0)
        self.assertEqual(planner.calls, [])


class SlashCommandsExitCleanlyTests(unittest.TestCase):
    """``/exit``, ``/quit``, ``exit``, ``quit`` all return 0 immediately."""

    def test_slash_exit_returns_zero(self) -> None:
        for cmd in ("/exit", "/quit", "exit", "quit"):
            with self.subTest(cmd=cmd):
                with TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    args = _stub_args()
                    planner = _RecordingPlanner()
                    rc = run_repl(
                        args,
                        base_dir=base,
                        profile_name="quick",
                        input_source=_QueueInput([cmd]),
                        planner_runner=planner,
                        output=_OutputSink(),
                    )
                self.assertEqual(rc, 0)
                self.assertEqual(planner.calls, [])


class WarmIntroFiresOncePerSessionTests(unittest.TestCase):
    """Issue #108 regression — four ``hi`` prompts must emit the intro once.

    The bug: ``run_interactive_shell`` reset ``turn = 0`` after emitting
    the warm intro, so the next ``turn += 1`` brought the counter back
    to ``1`` and the canned template re-fired on every greeting.

    The new design replaces the integer counter with a one-way
    ``WarmIntroState.intro_emitted`` flag the REPL holds for the life
    of the session. This test feeds the original reproducer
    (``hi``/``hi``/``hello``/``what can you do``) and asserts the
    intro fires *exactly* once, while non-open prompts dispatch to
    the planner.
    """

    def test_repeated_open_shaped_prompts_emit_intro_only_once(self) -> None:
        import os
        from unittest import mock as _mock

        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            args = _stub_args()
            planner = _RecordingPlanner()
            sink = _OutputSink()
            with _mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AISWMM_DISABLE_WELCOME", None)
                rc = run_repl(
                    args,
                    base_dir=base,
                    profile_name="quick",
                    input_source=_QueueInput(
                        ["hi", "hi", "hello", "what can you do", "/exit"]
                    ),
                    planner_runner=planner,
                    output=sink,
                )

        self.assertEqual(rc, 0)
        intro_lines = [
            line for line in sink.lines if "Agentic SWMM" in line and "stormwater" in line
        ]
        self.assertEqual(
            len(intro_lines),
            1,
            f"warm intro must fire exactly once per session; saw {len(intro_lines)} times. "
            f"Sink: {sink.lines!r}",
        )

    def test_task_shaped_prompt_dispatches_to_planner(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            args = _stub_args()
            planner = _RecordingPlanner()
            sink = _OutputSink()
            rc = run_repl(
                args,
                base_dir=base,
                profile_name="quick",
                input_source=_QueueInput(["run tecnopolo demo", "/exit"]),
                planner_runner=planner,
                output=sink,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(len(planner.calls), 1)
        self.assertEqual(planner.calls[0]["goal"], "run tecnopolo demo")


class NewSessionResetsWarmIntroStateTests(unittest.TestCase):
    """``/new-session`` re-arms the warm intro for the next greeting.

    The whole point of constructing a fresh ``WarmIntroState`` on
    ``/new-session`` is that a user who explicitly starts over should
    be greeted again. This is the *only* code path that re-arms the
    one-shot.
    """

    def test_new_session_re_arms_warm_intro(self) -> None:
        import os
        from unittest import mock as _mock

        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            args = _stub_args()
            planner = _RecordingPlanner()
            sink = _OutputSink()
            with _mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AISWMM_DISABLE_WELCOME", None)
                rc = run_repl(
                    args,
                    base_dir=base,
                    profile_name="quick",
                    input_source=_QueueInput(
                        ["hi", "/new-session", "hi", "/exit"]
                    ),
                    planner_runner=planner,
                    output=sink,
                )

        self.assertEqual(rc, 0)
        intro_lines = [
            line for line in sink.lines if "Agentic SWMM" in line and "stormwater" in line
        ]
        # ``hi`` fires once, ``/new-session`` resets, second ``hi`` fires once.
        self.assertEqual(
            len(intro_lines),
            2,
            f"intro should fire once per fresh session; saw {len(intro_lines)}. "
            f"Sink: {sink.lines!r}",
        )

    def test_new_session_emits_acknowledgement(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            args = _stub_args()
            planner = _RecordingPlanner()
            sink = _OutputSink()
            run_repl(
                args,
                base_dir=base,
                profile_name="quick",
                input_source=_QueueInput(["/new-session", "/exit"]),
                planner_runner=planner,
                output=sink,
            )
        # There must be some user-visible confirmation that the new
        # session command was accepted; we don't assert exact wording
        # so the UI string can evolve, only that *some* line went out.
        self.assertTrue(
            any("session" in line.lower() for line in sink.lines),
            f"/new-session should print a confirmation. Sink: {sink.lines!r}",
        )


if __name__ == "__main__":
    unittest.main()
