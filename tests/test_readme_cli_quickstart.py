"""README mentions every memory verb and the bootstrap caveat (PRD-08 A.2)."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"


class ReadmeMentionsMemoryVerbsTests(unittest.TestCase):
    """The quick-start table must show all 12 memory verbs."""

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

    def test_readme_contains_every_expected_invocation(self) -> None:
        text = README.read_text(encoding="utf-8")
        for phrase in self.EXPECTED_PHRASES:
            self.assertIn(
                phrase,
                text,
                msg=f"README missing {phrase!r}",
            )


class ReadmeBootstrapClarificationTests(unittest.TestCase):
    """The bootstrap-memory section must clarify citations + benchmarks scope."""

    def test_readme_clarifies_bootstrap_does_not_seed_citations(self) -> None:
        text = README.read_text(encoding="utf-8")
        self.assertIn("bootstrap memory", text)
        self.assertIn("citations.yaml", text)
        self.assertIn("reference_benchmarks.yaml", text)
        # The sentence must explicitly state that bootstrap does NOT
        # seed these files.
        self.assertIn("not seeded by this command", text.lower() if False else text)


if __name__ == "__main__":
    unittest.main()
