"""PRD-08 Phase B (audit #41, #42): list banner + interrupt cleanup hint.

* ``aiswmm list`` (no target) prints a richer banner that mentions
  the available targets AND the --help discovery path.
* ``KeyboardInterrupt`` during a long-running verb scans the run
  directory for partial state and surfaces a resume hint.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.cli import main as cli_main
from agentic_swmm.memory.run_progress import (
    list_partial_state_files as _list_partial_state_files,
)
from agentic_swmm.memory.run_progress import (
    summarize_progress_json as _summarise_progress_json,
)


def _capture(argv: list[str]) -> tuple[str, str, int]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(argv) or 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    return out.getvalue(), err.getvalue(), code


class ListBannerTests(unittest.TestCase):
    def test_list_no_target_emits_targets_banner(self) -> None:
        _, stderr, code = _capture(["list"])
        self.assertEqual(code, 2)
        # The new banner mentions both the cases target and the
        # --help discovery path so a stuck user knows what to type.
        self.assertIn("cases", stderr)
        self.assertIn("--help", stderr)

    def test_list_help_returns_zero_with_targets_listed(self) -> None:
        stdout, _, code = _capture(["list", "--help"])
        # argparse's --help exits 0 with the parser-rendered help block.
        self.assertEqual(code, 0)
        self.assertIn("cases", stdout)


class PartialStateScanTests(unittest.TestCase):
    def test_returns_empty_when_run_dir_is_none(self) -> None:
        self.assertEqual(_list_partial_state_files(None), [])

    def test_returns_empty_when_run_dir_missing(self) -> None:
        self.assertEqual(
            _list_partial_state_files(Path("/tmp/__nope__prd08_b_42__")),
            [],
        )

    def test_lists_progress_with_iteration_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "progress.json").write_text(
                json.dumps(
                    {
                        "iter_index": 47,
                        "total_iters": 100,
                        "best_objective_so_far": 0.61,
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "agent_trace.jsonl").write_text("{}\n", encoding="utf-8")
            (run_dir / "chat_note.md").write_text("# notes\n", encoding="utf-8")
            entries = _list_partial_state_files(run_dir)
            joined = "\n".join(entries)
            self.assertIn("progress.json", joined)
            self.assertIn("iter 47/100", joined)
            self.assertIn("best obj 0.61", joined)
            self.assertIn("agent_trace.jsonl", joined)
            self.assertIn("chat_note.md", joined)

    def test_summarise_progress_handles_missing_keys(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps({}), encoding="utf-8")
            self.assertIsNone(_summarise_progress_json(path))


class InterruptHintTests(unittest.TestCase):
    def test_interrupt_with_no_run_dir_prints_bare_message(self) -> None:
        # Build an argv that has no --run-dir. We invoke ``aiswmm
        # doctor`` (a cheap verb) but make the dispatch raise
        # KeyboardInterrupt deterministically.
        def boom(_args: argparse.Namespace) -> int:
            raise KeyboardInterrupt()

        with mock.patch(
            "agentic_swmm.commands.doctor.main", side_effect=boom
        ):
            _, stderr, code = _capture(["doctor"])
        self.assertEqual(code, 130)
        self.assertIn("Interrupted.", stderr)
        # No run-dir => no "Partial state saved" line.
        self.assertNotIn("Partial state saved to", stderr)

    def test_interrupt_with_run_dir_emits_partial_state_hint(self) -> None:
        def boom(_args: argparse.Namespace) -> int:
            raise KeyboardInterrupt()

        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            inp = run_dir / "model.inp"
            inp.write_text("[TITLE]\nstub\n", encoding="utf-8")
            (run_dir / "progress.json").write_text(
                json.dumps(
                    {
                        "iter_index": 12,
                        "total_iters": 50,
                        "best_objective_so_far": 0.42,
                    }
                ),
                encoding="utf-8",
            )
            argv = [
                "calibrate",
                "--quiet",
                "--run-id",
                "test-interrupt",
                "--total-iters",
                "1",
                "--inp",
                str(inp),
                "--param",
                "manning_n=0.01,0.02",
                "--run-dir",
                str(run_dir),
            ]
            with mock.patch(
                "agentic_swmm.commands.calibrate.main", side_effect=boom
            ):
                _, stderr, code = _capture(argv)
        self.assertEqual(code, 130)
        self.assertIn("Partial state saved to", stderr)
        self.assertIn("progress.json", stderr)
        self.assertIn("iter 12/50", stderr)
        self.assertIn("Resume with: aiswmm calibrate --run-id test-interrupt", stderr)


if __name__ == "__main__":
    unittest.main()
