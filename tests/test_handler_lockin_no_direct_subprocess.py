"""Lock-in: deterministic-SWMM ToolSpec handlers route through MCP.

PRD-Y user-story 12 + Done Criteria: no deterministic-SWMM tool may
shell out to ``python3 skills/...`` — each must be built by the
MCP-routed factory (``_make_mcp_routed_handler``), which forwards
through ``MCPPool.call_tool``.

Historical note: this file used to ``ast.parse`` the registry's source
looking for banned helper calls inside named handler functions. Those
functions no longer exist by name (the handlers are factory-built
closures), which made the AST walks vacuous — they iterated zero
matching nodes and passed unconditionally. The factory marker is the
authoritative fact now, and it is asserted through the registry's
public ``mcp_routing()`` query: replacing a factory-built handler with
a hand-rolled subprocess shim removes the marker and fails this test.
"""

from __future__ import annotations

from agentic_swmm.agent.tool_registry import AgentToolRegistry

# Deterministic-SWMM tool → the MCP server that owns its script.
# ADR-0006 D2: derived from the authoritative binding table instead of a
# third hand-maintained copy of the same facts. This test's contract is
# specifically about MCP-ROUTED tools (they must route through the pool,
# never spawn their script directly), so it derives the MCP subset, not
# the router table's direct-subprocess supplement.
from agentic_swmm.agent.mcp_coverage import EXPECTED_BINDINGS as _BINDINGS

_DETERMINISTIC_TO_SKILL = {b.tool_spec_name: b.mcp_server for b in _BINDINGS}


def test_deterministic_handlers_are_mcp_routed() -> None:
    registry = AgentToolRegistry()
    for tool_name, server in _DETERMINISTIC_TO_SKILL.items():
        routing = registry.mcp_routing(tool_name)
        assert routing is not None, (
            f"{tool_name} is not built via _make_mcp_routed_handler — "
            "its handler is a legacy subprocess shim"
        )
        assert routing["server"] == server, (
            f"{tool_name} routes to {routing['server']}, expected {server}"
        )


def test_mcp_routing_returns_none_for_in_process_and_unknown() -> None:
    registry = AgentToolRegistry()
    # apply_patch is a genuinely in-process handler (shells to git apply,
    # never to a skills script) — it must not claim MCP routing.
    assert registry.mcp_routing("apply_patch") is None
    assert registry.mcp_routing("no_such_tool") is None
