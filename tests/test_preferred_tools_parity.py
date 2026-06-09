"""C7 (issue #246): preferred_tools ↔ registry parity test.

Every ``preferred_tools`` entry across ALL intents in
``agent/config/intent_map.json`` must be either:
  (a) a registered name in ``AgentToolRegistry().names``, or
  (b) the literal ``"call_mcp_tool"``.

After PRs 1–2 + the reachability-batch fixes (PR 4) this must pass
with NO allowlist.  If an entry fails, that is a real stale hint that
needs evidence-based correction, not an allowlist addition.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_swmm.agent.tool_registry import AgentToolRegistry


_INTENT_MAP = Path(__file__).resolve().parents[1] / "agent" / "config" / "intent_map.json"


@pytest.fixture(scope="module")
def registry() -> AgentToolRegistry:
    return AgentToolRegistry()


@pytest.fixture(scope="module")
def intent_map() -> dict:
    return json.loads(_INTENT_MAP.read_text(encoding="utf-8"))


def test_preferred_tools_all_registered_or_call_mcp_tool(
    registry: AgentToolRegistry,
    intent_map: dict,
) -> None:
    """No preferred_tools entry should name a tool that the planner cannot call.

    Allowed values:
      - any name in ``AgentToolRegistry().names``
      - the literal string ``"call_mcp_tool"`` (the escape-hatch for
        intentionally-unregistered MCP servers like swmm-gis / swmm-params)
    """
    stale: list[tuple[str, str]] = []
    for intent in intent_map.get("intents", []):
        intent_id = intent.get("id", "<unknown>")
        for tool_name in intent.get("preferred_tools", []):
            if tool_name == "call_mcp_tool":
                continue  # by-design escape hatch — allowed
            if tool_name not in registry.names:
                stale.append((intent_id, tool_name))

    if stale:
        detail = "\n".join(
            f"  intent={intent_id!r}: preferred_tools entry {tool_name!r} not in registry"
            for intent_id, tool_name in stale
        )
        raise AssertionError(
            "Stale preferred_tools entries found in intent_map.json "
            "(update the entry or register the tool):\n" + detail
        )


def test_intent_map_is_valid_json() -> None:
    """intent_map.json must remain valid JSON (load_intent_map lru_cache
    raises ValueError at planner init on parse failure)."""
    data = json.loads(_INTENT_MAP.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "intents" in data


def test_intent_map_preferred_tools_coverage(
    registry: AgentToolRegistry,
    intent_map: dict,
) -> None:
    """Every intent must have a ``preferred_tools`` list (possibly empty)
    so the planner always has a hint to work from."""
    for intent in intent_map.get("intents", []):
        assert "preferred_tools" in intent, (
            f"intent {intent.get('id')!r} is missing 'preferred_tools' key"
        )
