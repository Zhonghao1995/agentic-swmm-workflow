"""Tests for ``aiswmm calibrate`` CLI (PRD-06 Phase C.5 wire-in)."""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.cli import main as cli_main


def _build_args(run_dir: Path, *, print_every: int = 2, total: int = 6) -> list[str]:
    return [
        "calibrate",
        "--run-id",
        "cli-r1",
        "--algorithm",
        "sceua",
        "--total-iters",
        str(total),
        "--checkpoint-every",
        "1",
        "--base-inp",
        str(run_dir / "model.inp"),
        "--observed-csv",
        str(run_dir / "obs.csv"),
        "--param",
        "manning_n=0.01,0.03",
        "--objective",
        "nse",
        "--run-dir",
        str(run_dir),
        "--progress",
        "--print-every",
        str(print_every),
    ]


class ProgressTtyTests(unittest.TestCase):
    def test_tty_prints_one_line_per_print_every(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                # Make the isatty() check inside the command return True.
                with mock.patch("agentic_swmm.commands.calibrate._is_tty", return_value=True):
                    rc = cli_main(_build_args(run_dir, print_every=2, total=6))
            self.assertEqual(rc, 0)
            text = buf.getvalue()
            # Expect 3 summary lines (iters 2/4/6), each contains "sceua iter".
            lines = [ln for ln in text.splitlines() if "sceua iter" in ln]
            self.assertEqual(len(lines), 3)
            # The trailing block is the JSON summary; strip the
            # summary lines and reparse the remainder.
            tail = "\n".join(
                ln for ln in text.splitlines() if "sceua iter" not in ln
            )
            payload = json.loads(tail)
            self.assertEqual(payload["run_id"], "cli-r1")
            self.assertEqual(payload["iterations_completed"], 6)


class ProgressNonTtyTests(unittest.TestCase):
    def test_non_tty_appends_to_agent_trace_jsonl(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                with mock.patch(
                    "agentic_swmm.commands.calibrate._is_tty", return_value=False
                ):
                    rc = cli_main(_build_args(run_dir, print_every=3, total=9))
            self.assertEqual(rc, 0)
            trace = run_dir / "agent_trace.jsonl"
            self.assertTrue(trace.is_file())
            lines = [
                json.loads(ln)
                for ln in trace.read_text("utf-8").splitlines()
                if ln.strip()
            ]
            # print-every=3 over 9 iters -> 3 trace lines (3/6/9).
            self.assertEqual(len(lines), 3)
            for entry in lines:
                self.assertEqual(entry["event"], "calibrate_progress")
                self.assertEqual(entry["run_id"], "cli-r1")


class ParamSpecValidationTests(unittest.TestCase):
    def test_bad_param_spec_returns_1(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            args = _build_args(run_dir, print_every=1, total=2)
            # Replace the param spec with a malformed entry.
            param_idx = args.index("--param")
            args[param_idx + 1] = "manning_n_bad_spec"
            buf = io.StringIO()
            with mock.patch("sys.stderr", buf):
                rc = cli_main(args)
            self.assertEqual(rc, 1)
            self.assertIn("--param", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
