"""Memory-file warmth + boundary-discipline contract (issue #59).

UX-4 rewrites the seven ``agent/memory/*.md`` files in a warmer first-
person voice while retaining their scientific-boundary terminology. The
PRD-product-ux-overhaul explicitly authorizes touching these files (the
earlier PRD-Z rule "do not touch agent/memory/*.md" is overridden for
this slice).

Two contracts are enforced:

1. Each of the seven files contains at least one first-person sentence
   (regex ``\\bI\\b`` — capital I as a standalone word).
2. Across the corpus, the boundary-discipline vocabulary
   ("runnable" / "checked" / "calibrated" / "validated" /
   "evidence boundary") appears at least 5 times in total. This keeps
   the warm rewrite honest: warmth must not erase the scientific
   guardrails.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MEMORY_DIR = Path(__file__).resolve().parents[1] / "agent" / "memory"

_FILES = (
    "soul.md",
    "identification_memory.md",
    "evidence_memory.md",
    "modeling_workflow_memory.md",
    "operational_memory.md",
    "user_bridge_memory.md",
    "README.md",
)

_BOUNDARY_PHRASES = (
    "runnable",
    "checked",
    "calibrated",
    "validated",
    "evidence boundary",
)


class MemoryWarmthTests(unittest.TestCase):
    def test_each_file_has_first_person_voice(self) -> None:
        """``\\bI\\b`` must appear at least once in every memory file.

        ``\\b`` keeps "implicit" / "input" / "is" from matching; we are
        looking specifically for the standalone pronoun "I".
        """
        pattern = re.compile(r"\bI\b")
        for name in _FILES:
            path = _MEMORY_DIR / name
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertRegex(
                    text,
                    pattern,
                    msg=f"{name} must contain at least one first-person sentence",
                )

    def test_boundary_vocabulary_preserved(self) -> None:
        """At least 5 boundary-discipline phrase occurrences across files.

        The phrases come from the evidence ladder; if a warm rewrite
        drops below this threshold the rewrite has likely sanded down
        the scientific-guardrail language.
        """
        total = 0
        for name in _FILES:
            text = (_MEMORY_DIR / name).read_text(encoding="utf-8").lower()
            for phrase in _BOUNDARY_PHRASES:
                total += text.count(phrase)
        self.assertGreaterEqual(
            total,
            5,
            msg=(
                "Boundary-discipline vocabulary must appear at least 5 times "
                f"across the seven memory files; found {total}."
            ),
        )

    def test_all_files_exist(self) -> None:
        for name in _FILES:
            path = _MEMORY_DIR / name
            with self.subTest(file=name):
                self.assertTrue(path.is_file(), f"missing memory file: {path}")


if __name__ == "__main__":
    unittest.main()
