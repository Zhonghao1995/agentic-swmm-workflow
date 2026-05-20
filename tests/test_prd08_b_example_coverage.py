"""PRD-08 Phase B (audit #38 cross-cutting + cross-cutting items).

* Smoke test: every verb that registered ``--example`` produces a
  non-empty copy-pasteable invocation on stdout and exits 0.
* README links to ``docs/memory_runtime.md`` and the file exists.
* Top-level ``aiswmm --help`` mentions the memory runtime docs path.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import unittest
from pathlib import Path

from agentic_swmm.cli import main as cli_main


def _capture(argv: list[str]) -> tuple[str, str, int]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(argv) or 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    return out.getvalue(), err.getvalue(), code


# Subcommand-scoped ``--example`` forms that PRD-08 A.2 shipped. These
# stay covered so the parent-level flag added in the residual audit-#38
# pass does not silently shadow them.
EXAMPLE_FLAG_VERBS = [
    ["bootstrap", "memory", "--example"],
    ["calibrate", "--example"],
    ["capabilities", "--example"],
    ["cite", "--example"],
    ["cite-param", "--example"],
    ["compare", "--example"],
    ["doctor", "--example"],
    ["run", "--example"],
    ["storm", "--example"],
    ["trace", "--example"],
    ["transfer", "--example"],
    ["uncertainty", "plan", "--example"],
]


def _registered_top_level_verbs() -> list[str]:
    """Return every verb name registered on the top-level CLI parser.

    Derived from the live ``argparse`` subparser registry so the test
    fails the moment a new verb lands without an ``--example`` flag.
    ``help`` is excluded — it is the help router, not a workflow verb,
    and ``--example`` is not meaningful for it.
    """
    from agentic_swmm.cli import build_parser

    parser = build_parser()
    for action in parser._actions:  # noqa: SLF001 - argparse has no public API
        if isinstance(action, argparse._SubParsersAction):
            return sorted(name for name in action.choices if name != "help")
    return []


class ExampleFlagCoverageTests(unittest.TestCase):
    """Audit #38: every registered verb honours ``--example``."""

    def test_subcommand_scoped_example_forms_still_work(self) -> None:
        for argv in EXAMPLE_FLAG_VERBS:
            with self.subTest(argv=argv):
                stdout, _, code = _capture(argv)
                self.assertEqual(code, 0, f"{argv} exit={code}")
                self.assertTrue(
                    stdout.strip(),
                    f"{argv} produced empty stdout",
                )
                # The example must look like a runnable aiswmm
                # invocation (starts with ``aiswmm `` so paste-in-shell
                # works).
                self.assertTrue(
                    stdout.strip().startswith("aiswmm "),
                    f"{argv} stdout does not start with 'aiswmm ': "
                    f"{stdout!r}",
                )

    def test_every_registered_verb_emits_non_empty_example(self) -> None:
        """``aiswmm <verb> --example`` works for *every* registered verb.

        Audit #38 residual: PRD-08 A.2 only wired the new memory verbs
        plus run/audit/plot. Legacy and expert verbs lacked the flag and
        exited 2. Every verb in ``cli.py`` must now answer ``--example``
        with a copy-pasteable invocation on stdout and exit 0.
        """
        verbs = _registered_top_level_verbs()
        self.assertTrue(verbs, "no verbs discovered on the CLI parser")
        for verb in verbs:
            argv = [verb, "--example"]
            with self.subTest(verb=verb):
                stdout, _, code = _capture(argv)
                self.assertEqual(code, 0, f"{argv} exit={code}")
                self.assertTrue(
                    stdout.strip(),
                    f"{argv} produced empty stdout",
                )
                self.assertTrue(
                    stdout.strip().startswith("aiswmm "),
                    f"{argv} stdout does not start with 'aiswmm ': "
                    f"{stdout!r}",
                )


class DocsLinkTests(unittest.TestCase):
    """README + --help epilog cross-link."""

    def test_readme_links_to_memory_runtime_md(self) -> None:
        readme = Path(__file__).resolve().parents[1] / "README.md"
        text = readme.read_text(encoding="utf-8")
        self.assertIn("docs/memory_runtime.md", text)
        # File must exist or the link is broken.
        memory_runtime = (
            readme.parent / "docs" / "memory_runtime.md"
        )
        self.assertTrue(
            memory_runtime.is_file(),
            f"docs/memory_runtime.md missing at {memory_runtime}",
        )

    def test_top_level_help_mentions_memory_runtime_docs(self) -> None:
        stdout, _, code = _capture(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("memory_runtime.md", stdout)


if __name__ == "__main__":
    unittest.main()
