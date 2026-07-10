"""Interactive turns write the ADR-0003 session header (leftover wired).

The single-shot path gained session.yaml + agent_snapshot.json in #330;
interactive turns (the date-dir / turn-dir model) were deferred. The
turn dir IS the session dir: ``agent_trace.jsonl`` lives there, so the
header belongs there too. These tests capture the shell's real
``planner_runner`` closure via a stubbed REPL and drive one turn with a
stubbed planner, asserting header lifecycle on success, failure, and
interruption.
"""
from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

import yaml

from agentic_swmm.agent import runtime_loop
from agentic_swmm.agent.session_header import AGENT_SNAPSHOT_NAME, SESSION_HEADER_NAME


def _args(session_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        planner="llm",
        provider=None,
        model=None,
        session_dir=session_dir,
        safe=False,
        quick=False,
        interactive=True,
        verbose=False,
        max_steps=8,
    )


class ShellTurnHeaderTests(unittest.TestCase):
    def _run_one_turn(self, planner_rc: Any) -> Path:
        """Boot the shell with a stubbed REPL, run one turn, return its dir."""
        captured: dict[str, Any] = {}

        def fake_repl(args: Any, **kwargs: Any) -> int:
            captured.update(kwargs)
            return 0

        turn_dirs: list[Path] = []

        def fake_planner(run_args, goal, session_dir, trace_path, registry, **kwargs):
            turn_dirs.append(session_dir)
            if isinstance(planner_rc, BaseException):
                raise planner_rc
            return planner_rc

        with TemporaryDirectory() as raw:
            base = Path(raw)
            with mock.patch.object(runtime_loop, "run_repl", fake_repl), mock.patch.object(
                runtime_loop, "run_openai_planner", fake_planner
            ), mock.patch.object(runtime_loop._welcome, "print_welcome"):
                rc = runtime_loop.run_interactive_shell(_args(base))
                self.assertEqual(rc, 0)
                planner_runner = captured["planner_runner"]
                if isinstance(planner_rc, BaseException):
                    with self.assertRaises(type(planner_rc)):
                        planner_runner(
                            _args(base), "run the tod creek model", Path("."), Path("."), None
                        )
                else:
                    planner_runner(
                        _args(base), "run the tod creek model", Path("."), Path("."), None
                    )
                self.assertEqual(len(turn_dirs), 1)
                turn_dir = turn_dirs[0]
                header = yaml.safe_load(
                    (turn_dir / SESSION_HEADER_NAME).read_text(encoding="utf-8")
                )
                snapshot_exists = (turn_dir / AGENT_SNAPSHOT_NAME).is_file()
            return header, snapshot_exists  # type: ignore[return-value]

    def test_successful_turn_gets_completed_header(self) -> None:
        header, snapshot_exists = self._run_one_turn(planner_rc=0)
        self.assertEqual(header["goal"], "run the tod creek model")
        self.assertEqual(header["planner"], "llm")
        self.assertEqual(header["status"], "completed")
        self.assertTrue(snapshot_exists)

    def test_failed_turn_gets_failed_header(self) -> None:
        header, _ = self._run_one_turn(planner_rc=1)
        self.assertEqual(header["status"], "failed")

    def test_interrupted_turn_gets_interrupted_header(self) -> None:
        header, _ = self._run_one_turn(planner_rc=KeyboardInterrupt())
        self.assertEqual(header["status"], "interrupted")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
