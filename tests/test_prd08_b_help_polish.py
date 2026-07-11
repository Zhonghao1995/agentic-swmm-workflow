"""PRD-08 Phase B: small help-text polish tests.

Covers audit items #22 (bootstrap memory next-line), #23 (uncertainty
epilog), #24 (calibrate help mentions progress.json), #25 (calibrate
--observed-csv default None).

The tests intentionally lean on the public CLI surface (``cli_main``
+ stdout capture) rather than the registration internals — that way
a refactor that keeps the user-visible help intact does not break
this suite.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.cli import main as cli_main


def _capture_help(argv: list[str]) -> tuple[str, str, int]:
    """Run ``cli_main(argv)`` and capture stdout + stderr.

    Returns ``(stdout, stderr, exit_code)``. ``--help`` triggers
    ``SystemExit(0)`` via argparse; we trap it so the tests can assert
    on the captured text.
    """
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(argv) or 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    return out.getvalue(), err.getvalue(), code


class BootstrapNextLineTests(unittest.TestCase):
    """Audit #22: ``aiswmm bootstrap memory`` ends with a Next: section."""

    def test_next_section_appears_in_default_text_output(self) -> None:
        with TemporaryDirectory() as tmp:
            stdout, _, code = _capture_help(
                ["bootstrap", "memory", "--dir", str(Path(tmp) / "m")]
            )
            self.assertEqual(code, 0)
            self.assertIn("Next:", stdout)
            self.assertIn("aiswmm doctor", stdout)
            self.assertIn("citations.yaml", stdout)

    def test_next_section_suppressed_under_json(self) -> None:
        with TemporaryDirectory() as tmp:
            stdout, _, code = _capture_help(
                [
                    "bootstrap",
                    "memory",
                    "--dir",
                    str(Path(tmp) / "m"),
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            # The JSON path is structured — the Next: block lives only
            # in the text path so JSON consumers are not surprised.
            self.assertNotIn("Next:", stdout)


class UncertaintyEpilogTests(unittest.TestCase):
    """Audit #23: ``aiswmm uncertainty --help`` carries an Examples block."""

    def test_uncertainty_parent_help_contains_examples_block(self) -> None:
        stdout, _, code = _capture_help(["uncertainty", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("Examples:", stdout)
        # The plan example should appear so a fresh user sees the
        # ``--param NAME=LOW,HIGH`` invocation without drilling down.
        self.assertIn("uncertainty plan", stdout)
        self.assertIn("--param", stdout)

    def test_uncertainty_plan_help_carries_its_own_example(self) -> None:
        stdout, _, code = _capture_help(["uncertainty", "plan", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("Example:", stdout)
        self.assertIn("manning_n", stdout)


class CalibrateHelpProgressJsonTests(unittest.TestCase):
    """Audit #24: ``aiswmm calibrate --help`` mentions progress.json."""

    def test_help_mentions_progress_json_and_agent_trace(self) -> None:
        stdout, _, code = _capture_help(["calibrate", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("progress.json", stdout)
        self.assertIn("agent_trace.jsonl", stdout)


class CalibrateObservedCsvOptionalTests(unittest.TestCase):
    """Audit #25: ``--observed-csv`` is optional while the stub is in place."""

    def test_calibrate_without_observed_csv_runs_to_completion(self) -> None:
        # Build a minimal invocation that omits ``--observed-csv``
        # entirely. The STUB banner is suppressed with ``--quiet`` so
        # stdout stays clean for the assertion.
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            inp = run_dir / "model.inp"
            inp.write_text("[TITLE]\nstub\n", encoding="utf-8")
            argv = [
                "calibrate",
                "--quiet",
                # ADR-0005: only the synthetic engine may omit --observed-csv.
                "--engine",
                "synthetic",
                "--run-id",
                "no-obs",
                "--total-iters",
                "1",
                "--inp",
                str(inp),
                "--param",
                "manning_n=0.01,0.02",
                "--run-dir",
                str(run_dir),
            ]
            _, _, code = _capture_help(argv)
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
