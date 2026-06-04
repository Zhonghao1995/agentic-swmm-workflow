"""UX: trim per-run chrome noise on ``aiswmm run``.

Two fixes:
  1. The "standard layout: 00_inputs/, ..." line is pure boilerplate printed on
     EVERY run (never changes; the layout is documented and the manifest already
     lists the real paths). Drop it.
  2. ``--quiet`` is documented as "Suppress chrome; only errors and structured
     output emitted", but the "run directory:" line printed unconditionally.
     Gate it behind not-quiet so --quiet actually suppresses chrome.

Drives ``run.main`` with a stubbed ``run_command`` (clean .rpt, no swmm5).
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


class RunChromeQuietTests(unittest.TestCase):
    def _invoke(self, quiet: bool) -> tuple[str, str]:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        inp = root / "model.inp"
        inp.write_text("[TITLE]\nx\n", encoding="utf-8")
        run_dir = root / "run"
        runner_dir = run_dir / "05_runner"
        rpt = runner_dir / "model.rpt"
        out = runner_dir / "model.out"

        original = run_cmd.run_command

        def _stub(command, *, check: bool = True):
            runner_dir.mkdir(parents=True, exist_ok=True)
            rpt.write_text("  EPA SWMM 5.2 (Build 5.2.4)\n  clean run, no errors\n", encoding="utf-8")
            out.write_bytes(b"")
            return CommandResult(
                command=list(command),
                return_code=0,
                started_at_utc="2026-05-19T00:00:00+00:00",
                finished_at_utc="2026-05-19T00:00:01+00:00",
                stdout=_runner_manifest(rpt, out),
                stderr="",
            )

        run_cmd.run_command = _stub  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(run_cmd, "run_command", original))

        args = argparse.Namespace(
            inp=inp, run_dir=run_dir, node="J1", rpt_name=None, out_name=None,
            quiet=quiet, case_id=None,
        )
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            try:
                run_cmd.main(args)
            except SystemExit:
                pass
        return out_buf.getvalue(), err_buf.getvalue()

    def test_standard_layout_boilerplate_removed(self) -> None:
        _, err = self._invoke(quiet=False)
        self.assertNotIn("standard layout:", err)

    def test_run_directory_shown_by_default(self) -> None:
        _, err = self._invoke(quiet=False)
        self.assertIn("run directory:", err)

    def test_quiet_suppresses_run_directory_chrome(self) -> None:
        out, err = self._invoke(quiet=True)
        self.assertNotIn("run directory:", err)
        # stdout (the JSON manifest) is structured output and must still print.
        self.assertIn("manifest_version", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
