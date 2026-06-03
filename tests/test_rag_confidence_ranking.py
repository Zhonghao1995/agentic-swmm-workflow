"""Phase I — RAG retrieval ranks by pattern confidence (recency-aware).

build_memory_corpus annotates each entry with ``pattern_confidence`` (a 0..1
score derived from the decay-aware lessons lifecycle) "so the retrieval layer
can use" it — but ``retrieve`` never did. A stale, low-confidence precedent
ranked identically to a fresh, high-confidence one for the same query, which
misleads the HITL reviewer reading recalled precedents.

This pins a gentle confidence weighting: for the same textual match, a
higher-confidence entry ranks first. Entries with no confidence annotation
are treated as neutral (factor 1.0), so existing un-annotated corpora and
fixtures keep their ordering.

Reusing ``pattern_confidence`` (already decay-aware) avoids introducing a
second, independent recency model.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_PATH = REPO_ROOT / "skills" / "swmm-rag-memory" / "scripts" / "rag_memory_lib.py"


def load_lib():
    spec = importlib.util.spec_from_file_location("rag_memory_lib", LIB_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EntryConfidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lib = load_lib()

    def test_takes_strongest_pattern(self) -> None:
        entry = {"pattern_confidence": {"a": 0.2, "b": 0.9}}
        self.assertAlmostEqual(self.lib.entry_confidence(entry), 0.9)

    def test_absent_confidence_is_neutral_one(self) -> None:
        self.assertEqual(self.lib.entry_confidence({}), 1.0)

    def test_empty_map_is_neutral_one(self) -> None:
        self.assertEqual(self.lib.entry_confidence({"pattern_confidence": {}}), 1.0)

    def test_clamps_into_unit_range(self) -> None:
        self.assertEqual(self.lib.entry_confidence({"pattern_confidence": {"a": 5.0}}), 1.0)


class RetrieveConfidenceRankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lib = load_lib()

    def _entry(self, source_path: str, confidence: float | None) -> dict:
        entry = {
            "tokens": ["peak", "flow", "continuity"],
            "text": "peak flow continuity issue",
            "source_path": source_path,
            "run_id": source_path,
        }
        if confidence is not None:
            entry["pattern_confidence"] = {"p": confidence}
        return entry

    def test_higher_confidence_ranks_first_for_same_match(self) -> None:
        # The low-confidence entry has an alphabetically-EARLIER source_path,
        # so it would win the score tiebreak without confidence weighting.
        # Confidence must override that and float the high-confidence one up.
        entries = [
            self._entry("aaa_low", 0.1),
            self._entry("zzz_high", 0.9),
        ]
        results = self.lib.retrieve(entries, "peak flow continuity", top_k=5)
        self.assertEqual(results[0]["source_path"], "zzz_high")
        self.assertEqual(results[1]["source_path"], "aaa_low")

    def test_unannotated_entries_keep_base_ordering(self) -> None:
        # No pattern_confidence anywhere -> neutral factor -> ordering is
        # whatever the base score + tiebreak produced (here: source_path).
        entries = [self._entry("aaa", None), self._entry("bbb", None)]
        results = self.lib.retrieve(entries, "peak flow continuity", top_k=5)
        self.assertEqual([r["source_path"] for r in results], ["aaa", "bbb"])

    def test_confidence_surfaced_in_result(self) -> None:
        entries = [self._entry("x", 0.7)]
        results = self.lib.retrieve(entries, "peak flow", top_k=5)
        self.assertAlmostEqual(results[0]["confidence"], 0.7)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
