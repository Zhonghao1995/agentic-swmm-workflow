"""The ``memory-retrieval`` intent routes recall questions to the store tools.

Issue #124 Part A wired this intent to the swmm-rag-memory skill and its
``retrieve_memory`` tool; the memory simplification (2026-09-26) retired
both, so the intent now names the agent-internal ``recall_memory`` and
``recall_session_history`` tools and no skill.
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


class IntentMapMemoryRetrievalTests(unittest.TestCase):
    def test_memory_retrieval_intent_points_at_the_store_tools(self) -> None:
        intents = {it["id"]: it for it in _load_intent_map()["intents"]}
        self.assertIn("memory-retrieval", intents)
        intent = intents["memory-retrieval"]
        self.assertEqual(intent["skills"], [])
        self.assertEqual(intent["preferred_tools"], ["recall_memory", "recall_session_history"])
        # A user typing "have I seen this before" should be a positive match.
        keywords = [k.lower() for k in intent.get("keywords", [])]
        self.assertTrue(
            any("seen" in k or "recall" in k or "lessons" in k for k in keywords),
            f"memory-retrieval keywords must cover recall vocab; got {keywords}",
        )

    def test_no_intent_names_a_retired_skill_or_tool(self) -> None:
        payload = _load_intent_map()
        self.assertNotIn("swmm-modeling-memory", payload["mcp_enabled_skills"])
        for intent in payload["intents"]:
            self.assertNotIn("swmm-modeling-memory", intent.get("skills", []))
            self.assertNotIn("swmm-rag-memory", intent.get("skills", []))
            for tool in ("summarize_memory", "retrieve_memory", "recall_memory_search"):
                self.assertNotIn(tool, intent.get("preferred_tools", []))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
