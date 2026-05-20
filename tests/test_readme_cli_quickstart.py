"""The CLI verb reference lives in docs/installation.md.

The reference table was relocated out of the README into the installation
guide. These tests guard that every memory verb and the bootstrap caveat
stay documented there.
"""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_GUIDE = REPO_ROOT / "docs" / "installation.md"


class CliVerbReferenceTests(unittest.TestCase):
    """The installation guide's CLI verbs section must list every verb."""

    EXPECTED_PHRASES = (
        "aiswmm compare",
        "aiswmm transfer",
        "aiswmm cite",
        "aiswmm storm",
        "aiswmm uncertainty plan",
        "aiswmm calibrate",
        "aiswmm bootstrap memory",
        "aiswmm doctor",
        "aiswmm cite-param",
        "aiswmm run",
        "aiswmm audit",
        "aiswmm plot",
    )

    def test_cli_guide_contains_every_expected_invocation(self) -> None:
        text = CLI_GUIDE.read_text(encoding="utf-8")
        for phrase in self.EXPECTED_PHRASES:
            self.assertIn(
                phrase,
                text,
                msg=f"docs/installation.md missing {phrase!r}",
            )


class BootstrapClarificationTests(unittest.TestCase):
    """The bootstrap-memory entry must clarify the citations + benchmarks scope."""

    def test_guide_clarifies_bootstrap_does_not_seed_citations(self) -> None:
        text = CLI_GUIDE.read_text(encoding="utf-8")
        self.assertIn("bootstrap memory", text)
        self.assertIn("citations.yaml", text)
        self.assertIn("reference_benchmarks.yaml", text)
        # The sentence must explicitly state that bootstrap does NOT
        # seed these files.
        self.assertIn("not seeded by this command", text)


if __name__ == "__main__":
    unittest.main()
