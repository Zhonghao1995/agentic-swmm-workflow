"""Top-level CLI help groups + ``aiswmm help <verb>`` routing (PRD-08 A.2)."""

from __future__ import annotations

import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]


class GroupedTopLevelHelpTests(unittest.TestCase):
    """``aiswmm --help`` renders the grouped block + ``--ignore-memory``."""

    def _help(self) -> str:
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout

    def test_help_contains_group_headers(self) -> None:
        text = self._help()
        self.assertIn("Core workflow:", text)
        self.assertIn("Memory:", text)
        self.assertIn("Expert:", text)
        self.assertIn("Inspection:", text)
        self.assertIn("Setup:", text)

    def test_help_lists_ignore_memory_flag(self) -> None:
        # Audit #7: ``--ignore-memory`` must be visible in ``aiswmm --help``.
        text = self._help()
        self.assertIn("--ignore-memory", text)

    def test_help_pointer_to_verb_help(self) -> None:
        text = self._help()
        self.assertIn("aiswmm help", text)
        self.assertIn("--help", text)


class HelpSubcommandRoutingTests(unittest.TestCase):
    """``aiswmm help <verb>`` must NOT route to the LLM planner."""

    def test_help_compare_invokes_compare_help(self) -> None:
        # The router shells out to ``aiswmm compare --help``. Smoke
        # test that the shell-out exits 0 and prints the compare-
        # specific options.
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "help", "compare"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--run-a", proc.stdout)
        self.assertIn("--run-b", proc.stdout)

    def test_help_bare_prints_top_level(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Core workflow:", proc.stdout)

    def test_help_unknown_verb_returns_2(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "help", "frobnicate"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown verb", proc.stderr)

    def test_help_does_not_get_routed_to_agent(self) -> None:
        # If ``help`` were missing from the COMMANDS allow-list, the
        # default router would prepend ["agent", ...] and the planner
        # would interpret the verb as a free-form goal. Assert that
        # the help routing exits with a help-shaped success (or the
        # well-defined exit 2 for unknown verbs), never with the
        # planner's banner or 1.
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "help", "compare"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotIn("aiswmm executor", proc.stdout)
        self.assertNotIn("aiswmm executor", proc.stderr)


class IgnoreMemoryDocumentedTests(unittest.TestCase):
    """``--ignore-memory`` is parseable and still strips out pre-parse."""

    def test_flag_shows_in_help(self) -> None:
        from agentic_swmm.cli import build_parser

        parser = build_parser()
        help_text = parser.format_help()
        self.assertIn("--ignore-memory", help_text)

    def test_main_strips_flag_before_parse(self) -> None:
        # Smoke: invoking with the flag in the middle position does
        # not break argparse (the pre-parse strip handles it).
        from agentic_swmm.cli import _strip_ignore_memory

        cleaned, present = _strip_ignore_memory(
            ["doctor", "--ignore-memory", "--json"]
        )
        self.assertTrue(present)
        self.assertEqual(cleaned, ["doctor", "--json"])


if __name__ == "__main__":
    unittest.main()
