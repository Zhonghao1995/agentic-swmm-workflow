"""Issue #124 Parts A + B1: intent_map gains ``memory-retrieval`` and ``lid-optimization`` intents.

The PRD ships agent-callable ToolSpecs for swmm-rag-memory and
swmm-lid-optimization; the planner only routes prompts to them when an
intent in ``agent/config/intent_map.json`` matches. This test asserts the
two new intents are wired and reference the correct skills and tools.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_intent_map():
    return json.loads(
        (REPO_ROOT / "agent" / "config" / "intent_map.json").read_text(encoding="utf-8")
    )


class IntentMapRagAndLidIntentsTests(unittest.TestCase):
    def test_memory_retrieval_intent_present(self) -> None:
        intents = {it["id"]: it for it in _load_intent_map()["intents"]}
        self.assertIn("memory-retrieval", intents)
        intent = intents["memory-retrieval"]
        self.assertIn("swmm-rag-memory", intent["skills"])
        self.assertIn("retrieve_memory", intent["preferred_tools"])
        # A user typing "have I seen this before" should be a positive match.
        keywords = [k.lower() for k in intent.get("keywords", [])]
        self.assertTrue(
            any("seen" in k or "recall" in k or "lessons" in k for k in keywords),
            f"memory-retrieval keywords must cover recall vocab; got {keywords}",
        )

    def test_lid_optimization_intent_present(self) -> None:
        intents = {it["id"]: it for it in _load_intent_map()["intents"]}
        self.assertIn("lid-optimization", intents)
        intent = intents["lid-optimization"]
        self.assertIn("swmm-lid-optimization", intent["skills"])
        self.assertIn("propose_lid_scenarios", intent["preferred_tools"])
        keywords = [k.lower() for k in intent.get("keywords", [])]
        self.assertIn("lid", keywords)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
