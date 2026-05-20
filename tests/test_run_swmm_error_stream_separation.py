"""Audit #1 residual: ``aiswmm run`` stdout must stay pure JSON on a SWMM error.

When a SWMM solver ERROR occurs the honesty layer exits non-zero and
surfaces the verbatim error. A pipeline doing ``aiswmm run > result.json``
must still get a clean, parseable JSON manifest on ``stdout`` — the error
text and every human-readable chrome line belong on ``stderr``.

These tests drive ``run.main`` with a stubbed ``run_command`` (no real
swmm5 dependency) whose ``stdout`` is the runner manifest JSON pointing
at a ``.rpt`` we author by hand.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.commands import run as run_cmd
from agentic_swmm.utils.subprocess_runner import CommandResult


def _runner_manifest(rpt: Path, out: Path) -> str:
    """Build the JSON ``swmm_runner.py`` would print to its stdout."""
    return json.dumps(
        {
            "manifest_version": "1.0",
            "swmm5": {"cmd": "swmm5", "version": "5.2.4"},
            "files": {
                "rpt": str(rpt),
                "out": str(out),
                "stdout": str(rpt.parent / "stdout.txt"),
                "stderr": str(rpt.parent / "stderr.txt"),
            },
            "metrics": {
                "peak": {"node": "J1", "peak": None, "source": None},
                "continuity": {"continuity_error_percent": {}},
            },
            "return_code": 0,
        },
        indent=2,
    )


class RunSwmmErrorStreamSeparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.inp = self.tmp / "broken.inp"
        self.inp.write_text("[TITLE]\nbroken\n", encoding="utf-8")
        self.run_dir = self.tmp / "run"

    def _invoke_with_swmm_error(self) -> tuple[str, str, int]:
        """Run ``run.main`` with a stubbed runner that emitted SWMM errors."""
        runner_dir = self.run_dir / "05_runner"
        rpt = runner_dir / "model.rpt"
        out = runner_dir / "model.out"

        original_run_command = run_cmd.run_command

        def _stub_run_command(command, *, check: bool = True):
            # ``run.main`` has already created ``05_runner`` by the time
            # it shells out; author a .rpt carrying canonical SWMM
            # ``ERROR <n>:`` lines so the honesty layer trips.
            runner_dir.mkdir(parents=True, exist_ok=True)
            rpt.write_text(
                "  EPA SWMM 5.2\n"
                "  ERROR 205: invalid keyword at line 11 of input file:\n"
                "  ERROR 205: invalid keyword at line 12 of input file:\n",
                encoding="utf-8",
            )
            out.write_bytes(b"")
            return CommandResult(
                command=list(command),
                return_code=0,
                started_at_utc="2026-05-19T00:00:00+00:00",
                finished_at_utc="2026-05-19T00:00:01+00:00",
                stdout=_runner_manifest(rpt, out),
                stderr="",
            )

        run_cmd.run_command = _stub_run_command  # type: ignore[assignment]
        self.addCleanup(
            lambda: setattr(run_cmd, "run_command", original_run_command)
        )

        args = argparse.Namespace(
            inp=self.inp,
            run_dir=self.run_dir,
            node="J1",
            rpt_name=None,
            out_name=None,
            quiet=False,
            case_id=None,
        )
        out_buf, err_buf = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(
            err_buf
        ):
            try:
                code = run_cmd.main(args) or 0
            except SystemExit as exc:  # pragma: no cover - defensive
                code = int(exc.code or 0)
        return out_buf.getvalue(), err_buf.getvalue(), code

    def test_stdout_is_pure_parseable_json_manifest(self) -> None:
        stdout, _, _ = self._invoke_with_swmm_error()
        # The whole of stdout must parse as a single JSON document — a
        # pipeline doing ``aiswmm run > result.json`` depends on this.
        parsed = json.loads(stdout)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed.get("manifest_version"), "1.0")

    def test_stdout_carries_no_error_or_chrome_text(self) -> None:
        stdout, _, _ = self._invoke_with_swmm_error()
        self.assertNotIn("ERROR", stdout)
        self.assertNotIn("error:", stdout)
        # Human-readable chrome lines must not pollute the JSON either.
        self.assertNotIn("run directory:", stdout)
        self.assertNotIn("standard layout:", stdout)

    def test_stderr_carries_the_verbatim_swmm_error_lines(self) -> None:
        _, stderr, _ = self._invoke_with_swmm_error()
        self.assertIn(
            "ERROR 205: invalid keyword at line 11 of input file:", stderr
        )
        self.assertIn(
            "ERROR 205: invalid keyword at line 12 of input file:", stderr
        )
        # The summary line is the honesty layer's verdict — also stderr.
        self.assertIn("error: SWMM reported 2 error(s)", stderr)

    def test_exit_code_is_one(self) -> None:
        _, _, code = self._invoke_with_swmm_error()
        self.assertEqual(code, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
