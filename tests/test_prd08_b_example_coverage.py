"""PRD-08 Phase B (audit #38 cross-cutting + cross-cutting items).

* Smoke test: every verb that registered ``--example`` produces a
  non-empty copy-pasteable invocation on stdout and exits 0.
* README links to ``docs/memory_runtime.md`` and the file exists.
* Top-level ``aiswmm --help`` mentions the memory runtime docs path.
"""

from __future__ import annotations

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


# Every verb (or subcommand) that has been wired with ``register_example_flag``.
# The list is hand-maintained so we notice when a new verb is added without
# the example flag. The smoke test asserts each invocation exits 0 with
# non-empty stdout, which is the user-visible contract.
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


class ExampleFlagCoverageTests(unittest.TestCase):
    """Audit #38: every verb that registered ``--example`` honours it."""

    def test_every_registered_verb_emits_non_empty_example(self) -> None:
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
