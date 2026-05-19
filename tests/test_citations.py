"""Tests for ``agentic_swmm.memory.citations`` (PRD-06 Phase B.2).

The citation library backs the ``citation`` leaves in
``reference_benchmarks.yaml``. The loader has to be tolerant of missing
files (fresh project), malformed YAML, and partial entries.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.citations import (
    Citation,
    load_citations,
    recall_citation,
)


_SAMPLE_YAML = """\
schema_version: "1.0"

verified_example:
  authors: "Doe, J."
  year: 1999
  title: "Verified worked example"
  work: "Internal Memo"
  locator: "Section 1"
  url: ""
  verified_by: "maintainer"
  verified_on: "2026-05-19"

placeholder_example:
  authors: "<pending>"
  year: 0
  title: "<pending>"
  work: "<pending>"
  locator: "<pending>"
  url: ""
  verified_by: ""
  verified_on: ""
"""


class LoadCitationsTests(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.yaml"
            entries = load_citations(path)
        self.assertEqual(entries, {})

    def test_loads_one_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.yaml"
            path.write_text(_SAMPLE_YAML, encoding="utf-8")
            entries = load_citations(path)
        self.assertIn("verified_example", entries)
        self.assertIn("placeholder_example", entries)
        # schema_version is not a citation entry.
        self.assertNotIn("schema_version", entries)

    def test_malformed_yaml_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(":\n:::not yaml", encoding="utf-8")
            entries = load_citations(path)
        self.assertEqual(entries, {})

    def test_non_dict_top_level_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.yaml"
            path.write_text("- one\n- two\n", encoding="utf-8")
            entries = load_citations(path)
        self.assertEqual(entries, {})

    def test_scalar_top_level_keys_are_ignored(self) -> None:
        # A loose ``schema_version: "1.0"`` plus a single entry — the
        # scalar must not appear in the returned dict.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.yaml"
            path.write_text(
                'schema_version: "1.0"\n\nentry_a:\n  authors: "A"\n'
                "  year: 2026\n  title: \"T\"\n  work: \"W\"\n  locator: \"L\"\n",
                encoding="utf-8",
            )
            entries = load_citations(path)
        self.assertEqual(set(entries.keys()), {"entry_a"})

    def test_partial_entry_does_not_raise(self) -> None:
        # An entry missing optional fields should still load.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "partial.yaml"
            path.write_text(
                'partial_entry:\n  authors: "A"\n  year: 2010\n  title: "T"\n',
                encoding="utf-8",
            )
            entries = load_citations(path)
        self.assertIn("partial_entry", entries)
        partial = entries["partial_entry"]
        self.assertEqual(partial.authors, "A")
        self.assertEqual(partial.year, 2010)
        self.assertEqual(partial.work, "")
        self.assertEqual(partial.locator, "")

    def test_non_integer_year_falls_back_to_zero(self) -> None:
        # A typo in the year field must not raise — citation lookups
        # are surfaced to humans, who will see the bogus value and fix
        # the YAML.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_year.yaml"
            path.write_text(
                'bad_year_entry:\n  authors: "A"\n  year: "notanumber"\n  title: "T"\n',
                encoding="utf-8",
            )
            entries = load_citations(path)
        self.assertEqual(entries["bad_year_entry"].year, 0)


class RecallCitationTests(unittest.TestCase):
    def test_recall_hit_returns_citation(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.yaml"
            path.write_text(_SAMPLE_YAML, encoding="utf-8")
            entry = recall_citation(path, "verified_example")
        self.assertIsInstance(entry, Citation)
        assert entry is not None  # narrow for mypy / linter
        self.assertEqual(entry.key, "verified_example")
        self.assertEqual(entry.year, 1999)

    def test_recall_miss_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.yaml"
            path.write_text(_SAMPLE_YAML, encoding="utf-8")
            entry = recall_citation(path, "no_such_key")
        self.assertIsNone(entry)

    def test_recall_empty_key_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.yaml"
            path.write_text(_SAMPLE_YAML, encoding="utf-8")
            entry = recall_citation(path, "   ")
        self.assertIsNone(entry)

    def test_recall_missing_file_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.yaml"
            entry = recall_citation(path, "any")
        self.assertIsNone(entry)


class CitationVerificationTests(unittest.TestCase):
    def test_is_verified_true_when_both_fields_populated(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.yaml"
            path.write_text(_SAMPLE_YAML, encoding="utf-8")
            entry = recall_citation(path, "verified_example")
        assert entry is not None
        self.assertTrue(entry.is_verified)

    def test_is_verified_false_when_placeholder(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.yaml"
            path.write_text(_SAMPLE_YAML, encoding="utf-8")
            entry = recall_citation(path, "placeholder_example")
        assert entry is not None
        self.assertFalse(entry.is_verified)

    def test_to_dict_includes_is_verified(self) -> None:
        entry = Citation(
            key="k",
            authors="A",
            year=2026,
            title="T",
            work="W",
            locator="L",
            verified_by="me",
            verified_on="2026-05-19",
        )
        payload = entry.to_dict()
        self.assertIn("is_verified", payload)
        self.assertTrue(payload["is_verified"])


class ShippedLibraryTests(unittest.TestCase):
    """The repo-shipped citations.yaml must be a valid (even if schema-only) file."""

    def test_shipped_library_loads(self) -> None:
        # Locate the repo-shipped file relative to this test file.
        # tests/test_citations.py -> ../memory/modeling-memory/citations.yaml
        repo_yaml = (
            Path(__file__).resolve().parents[1]
            / "memory"
            / "modeling-memory"
            / "citations.yaml"
        )
        if not repo_yaml.is_file():
            self.skipTest(f"shipped citations.yaml not found at {repo_yaml}")
        entries = load_citations(repo_yaml)
        # Must parse without error and yield at least the worked example.
        self.assertIsInstance(entries, dict)
        self.assertIn("worked_example_pending_verification", entries)
        # Worked-example entry must not be marked verified (it ships with
        # placeholder ``verified_by`` and ``verified_on``).
        self.assertFalse(
            entries["worked_example_pending_verification"].is_verified
        )


if __name__ == "__main__":
    unittest.main()
