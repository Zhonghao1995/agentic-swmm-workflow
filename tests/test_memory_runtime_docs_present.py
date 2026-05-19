"""Docs presence smoke tests (PRD-06 Phase D.3).

The runtime references two engineering docs; if they vanish, the
README link in the bootstrap output and the installation.md mention
point at nothing. These tests only check the docs exist and reference
the expected substrate — they do not pin prose.
"""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class DocsPresentTests(unittest.TestCase):
    def test_memory_runtime_doc_exists(self) -> None:
        doc = REPO_ROOT / "docs" / "memory_runtime.md"
        self.assertTrue(doc.is_file(), f"missing engineering doc: {doc}")
        content = doc.read_text(encoding="utf-8")
        # Smoke: the doc should reference each of the substrate files
        # the bootstrap command creates.
        for store in (
            "parametric_memory.jsonl",
            "calibration_memory.jsonl",
            "negative_lessons.jsonl",
            "project_overrides.yaml",
            "citations.yaml",
        ):
            self.assertIn(store, content, f"doc should mention {store}")
        # Smoke: the four confidence quadrants must appear.
        for quadrant in ("auto_complete", "memory_informed", "llm", "hitl"):
            self.assertIn(quadrant, content)
        # Smoke: the audit trail file is named.
        self.assertIn("memory_trace.jsonl", content)

    def test_memory_runtime_cli_doc_exists(self) -> None:
        doc = REPO_ROOT / "docs" / "memory_runtime_cli.md"
        self.assertTrue(doc.is_file(), f"missing CLI doc: {doc}")
        content = doc.read_text(encoding="utf-8")
        # Smoke: every documented verb has its own subsection header.
        for verb in (
            "aiswmm compare",
            "aiswmm cite",
            "aiswmm storm",
            "aiswmm transfer",
            "aiswmm uncertainty plan",
            "aiswmm bootstrap memory",
        ):
            self.assertIn(verb, content, f"CLI doc should document {verb!r}")

    def test_installation_md_links_to_cli_doc(self) -> None:
        doc = REPO_ROOT / "docs" / "installation.md"
        self.assertTrue(doc.is_file())
        content = doc.read_text(encoding="utf-8")
        self.assertIn("memory_runtime_cli.md", content)


if __name__ == "__main__":
    unittest.main()
