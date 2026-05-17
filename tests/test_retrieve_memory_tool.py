"""Issue #124 Part A: ``retrieve_memory`` ToolSpec wiring.

The agent runtime previously had no path to the swmm-rag-memory skill. A user
asking "have I seen this kind of failure before?" got the LLM's guess instead
of grounded retrieval. This test asserts that:

1. ``retrieve_memory`` is in the registered ToolSpec set.
2. Its schema declares the ``query`` argument as required.
3. The handler validates missing ``query`` and returns a ``_failure`` shape
   without ever shelling out (so the planner can recover).
"""

from __future__ import annotations

import unittest

from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolCall


def _registered_specs() -> dict:
    registry = AgentToolRegistry()
    # Internal access is fine inside tests — the public ``schemas()`` shape
    # collapses to dicts and discards the handler reference we need below.
    return registry._tools  # noqa: SLF001


class RetrieveMemoryToolSpecTests(unittest.TestCase):
    def test_retrieve_memory_is_registered(self) -> None:
        specs = _registered_specs()
        self.assertIn(
            "retrieve_memory",
            specs,
            "retrieve_memory ToolSpec must be registered (Issue #124 Part A)",
        )
        spec = specs["retrieve_memory"]
        self.assertTrue(spec.is_read_only, "retrieve_memory is read-only retrieval")
        # Description should mention the skill so planner can map intent.
        self.assertIn("swmm-rag-memory", spec.description)

    def test_retrieve_memory_schema_requires_query(self) -> None:
        spec = _registered_specs()["retrieve_memory"]
        schema = spec.parameters
        self.assertIn("query", schema.get("properties", {}))
        self.assertIn("query", schema.get("required", []))

    def test_retrieve_memory_handler_fails_soft_on_missing_query(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            session_dir = Path(td)
            spec = _registered_specs()["retrieve_memory"]
            call = ToolCall(name="retrieve_memory", args={})
            result = spec.handler(call, session_dir)
        # Fail-soft: planner sees ``ok=False`` and a summary that mentions
        # the missing argument so the recovery path can prompt the user.
        self.assertFalse(result.get("ok", True))
        haystack = (str(result.get("summary", "")) + " " + str(result.get("error", ""))).lower()
        self.assertIn("query", haystack)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
