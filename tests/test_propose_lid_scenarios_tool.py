"""Issue #124 Part B1: ``propose_lid_scenarios`` ToolSpec wiring.

The swmm-lid-optimization skill shipped with scripts, an example config, and
a benchmark, but no agent-callable entry point. A prompt like "explore LID
placement on my watershed" routed nowhere. This test asserts that:

1. ``propose_lid_scenarios`` is registered.
2. Its schema declares the required inputs (``base_inp``, ``config``).
3. The handler validates missing inputs and returns a ``_failure`` shape so
   the planner can prompt the user for the missing artefacts.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolCall


def _registered_specs() -> dict:
    return AgentToolRegistry()._tools  # noqa: SLF001


class ProposeLidScenariosToolSpecTests(unittest.TestCase):
    def test_propose_lid_scenarios_is_registered(self) -> None:
        specs = _registered_specs()
        self.assertIn(
            "propose_lid_scenarios",
            specs,
            "propose_lid_scenarios ToolSpec must be registered (Issue #124 Part B1)",
        )
        spec = specs["propose_lid_scenarios"]
        self.assertIn("swmm-lid-optimization", spec.description)

    def test_propose_lid_scenarios_schema_requires_base_inp_and_config(self) -> None:
        spec = _registered_specs()["propose_lid_scenarios"]
        schema = spec.parameters
        self.assertIn("base_inp", schema.get("properties", {}))
        self.assertIn("config", schema.get("properties", {}))
        required = set(schema.get("required", []))
        self.assertIn("base_inp", required)
        self.assertIn("config", required)

    def test_propose_lid_scenarios_handler_fails_soft_on_missing_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session_dir = Path(td)
            spec = _registered_specs()["propose_lid_scenarios"]
            call = ToolCall(name="propose_lid_scenarios", args={})
            result = spec.handler(call, session_dir)
        self.assertFalse(result.get("ok", True))
        haystack = (str(result.get("summary", "")) + " " + str(result.get("error", ""))).lower()
        self.assertTrue(
            "base_inp" in haystack or "config" in haystack,
            f"summary should name the missing argument; got {result.get('summary')!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
