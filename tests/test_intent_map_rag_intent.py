"""Issue #124 Part A: intent_map gains the ``memory-retrieval`` intent.

The PRD ships an agent-callable ToolSpec for swmm-rag-memory so the planner
can route prompts like "have I seen this failure before" to ``retrieve_memory``
when an intent in ``agent/config/intent_map.json`` matches. This test asserts
the intent is wired and references the correct skill and tool.
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


class IntentMapRagIntentTests(unittest.TestCase):
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
