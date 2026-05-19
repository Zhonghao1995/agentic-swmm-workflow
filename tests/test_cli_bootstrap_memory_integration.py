"""CLI integration test for ``aiswmm bootstrap memory`` (PRD-06 Phase D.4).

Pins that the top-level CLI routes ``bootstrap memory`` to the
bootstrap_memory module rather than into the default-router's
agent codepath.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentic_swmm.cli import COMMANDS, build_parser, main


class CliBootstrapMemoryTests(unittest.TestCase):
    def test_bootstrap_in_commands_set(self) -> None:
        # The default-router checks this set to decide whether to
        # punt the command to the agent. ``bootstrap`` must be here.
        self.assertIn("bootstrap", COMMANDS)

    def test_parser_recognises_bootstrap_memory(self) -> None:
        parser = build_parser()
        with tempfile.TemporaryDirectory() as tmp:
            args = parser.parse_args(
                ["bootstrap", "memory", "--dir", tmp]
            )
            self.assertEqual(args.command, "bootstrap")
            self.assertEqual(args.bootstrap_target, "memory")
            self.assertEqual(args.target_dir, Path(tmp))

    def test_cli_main_runs_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modeling-memory"
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["bootstrap", "memory", "--dir", str(target)])
            self.assertEqual(code, 0)
            self.assertTrue((target / "parametric_memory.jsonl").is_file())
            self.assertIn("created", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
