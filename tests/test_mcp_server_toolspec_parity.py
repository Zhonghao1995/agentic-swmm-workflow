"""Issue #235 Part 4a: every ``mcp/<server>/`` must be planner-reachable.

Background
----------
``agentic_swmm/agent/mcp_coverage.py`` (consumed by
``tests/test_mcp_coverage_matrix.py`` and ``aiswmm mcp coverage``) only
walks a hand-curated ``EXPECTED_BINDINGS`` table. That table proves the
*listed* subprocess-Python ToolSpecs route to a real ``server.tool(...)``
in the right ``mcp/<server>/server.js`` -- but it says nothing about a
server that was never added to the table. Ship a brand new
``mcp/<name>/`` directory, register it in
``agentic_swmm/runtime/registry.py:MCP_SERVERS`` and
``agent/config/intent_map.json:mcp_enabled_skills``, and CI stays green
even though no ToolSpec ever routes a call to it -- the LLM can only
reach it through the generic ``call_mcp_tool``/``list_mcp_tools`` bridge
(``ToolSpec("call_mcp_tool", ...)`` in
``agentic_swmm/agent/tool_registry.py``), discovered dynamically at
runtime rather than offered as a first-class tool. That is a "dark" MCP
server, and nothing before this test asserted it was a deliberate choice.

This test is the missing gate. It is the mirror image of
``tests/test_intent_map_mcp_enabled_skills_covers_all_servers.py``
(which proves the intent map's ``mcp_enabled_skills`` is a superset of
``mcp/*/``): here we prove every ``mcp/*/`` directory is either
ToolSpec-routed or explicitly, visibly opted out of routing.

Ground truth for "does a ToolSpec route to this server?"
----------------------------------------------------------
``agentic_swmm/agent/tool_handlers/_shared.py:_make_mcp_routed_handler``
is the factory every MCP-routed ToolSpec handler is built from; it tags
the closure with ``handler._mcp_routing = {"server": ..., "tool": ...}``.
``AgentToolRegistry.mcp_routing(name)`` (in
``agentic_swmm/agent/tool_registry.py``) is the public query surface
over that tag -- ``tests/test_handler_lockin_no_direct_subprocess.py``
already leans on it instead of parsing source or reading closure
internals, and this test does the same rather than re-deriving the
fact via a source-level regex.

Discovery (2026-07-07): walking every ``ToolSpec`` name in
``AgentToolRegistry().names`` through ``.mcp_routing(name)`` and
collecting the ``server`` values currently yields: swmm-builder,
swmm-calibration, swmm-climate, swmm-experiment-audit,
swmm-modeling-memory, swmm-network, swmm-plot, swmm-runner,
swmm-uncertainty (9 servers). ``swmm-calibration`` and
``swmm-uncertainty`` used to be dark too -- see the "dark-MCP
registration (PR 1/2, issue #246)" comments in
``agentic_swmm/agent/mcp_coverage.py`` -- but both were wired up since.
Cross-referencing that 9-server set against the 11 directories actually
shipped under ``mcp/`` leaves exactly two still dark: ``swmm-gis`` and
``swmm-params``. That matches the escape-hatch comment already living
in ``tests/test_preferred_tools_parity.py``, which names
``call_mcp_tool`` as "the escape-hatch for intentionally-unregistered
MCP servers like swmm-gis / swmm-params" -- independent corroboration
that these two, and only these two, are today's dark servers.

Shrinking ``_ALLOWLISTED_CALL_MCP_TOOL_ONLY_SERVERS`` to the empty set
is the goal: every entry removed should be replaced by a real ToolSpec
built via ``_make_mcp_routed_handler``, never by a justification for
keeping the entry around.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agentic_swmm.agent.tool_registry import AgentToolRegistry


REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = REPO_ROOT / "mcp"


# Servers shipped under mcp/ that are deliberately reachable ONLY through
# the generic call_mcp_tool / list_mcp_tools bridge -- no dedicated
# ToolSpec routes to them yet. Each entry is a real product gap, not a
# permanent design decision; see the module docstring for how this list
# was derived. THE GOAL IS TO SHRINK THIS SET TO EMPTY, NOT TO GROW IT --
# adding a server here should come with a plan to give it a real
# ToolSpec, not a shrug.
_ALLOWLISTED_CALL_MCP_TOOL_ONLY_SERVERS: frozenset[str] = frozenset(
    {
        "swmm-gis",  # skills/swmm-gis: no _make_mcp_routed_handler ToolSpec yet.
        "swmm-params",  # skills/swmm-params: no _make_mcp_routed_handler ToolSpec yet.
    }
)


def _servers_shipped_on_disk() -> set[str]:
    """Every ``mcp/<name>/`` directory that is an actual server -- the
    ground truth for "exists".

    Mirrors ``test_intent_map_mcp_enabled_skills_covers_all_servers.py``:
    skip non-directories (e.g. a stray ``mcp/.DS_Store``), dotfiles, and
    non-server helper directories (``mcp/_lib/``, the shared
    ``python-tool-server.mjs`` prologue -- ADR-0006 D5) that hold no
    ``server.js`` of their own.
    """

    return {
        child.name
        for child in MCP_ROOT.iterdir()
        if child.is_dir()
        and not child.name.startswith(".")
        and (child / "server.js").is_file()
    }


def _servers_with_toolspec_routing() -> set[str]:
    """Every MCP server named by at least one ToolSpec's routing metadata.

    Queries the live registry rather than grepping source: handlers built
    by ``_make_mcp_routed_handler`` self-report their ``(server, tool)``
    pair through ``AgentToolRegistry.mcp_routing``, so this reflects
    actual planner-reachable routing regardless of which module a given
    handler function happens to live in today (``tool_registry.py``
    itself, or one of the ``tool_handlers/*.py`` modules it wires in).
    """

    registry = AgentToolRegistry()
    servers: set[str] = set()
    for name in registry.names:
        routing = registry.mcp_routing(name)
        if routing:
            servers.add(routing["server"])
    return servers


class McpServerToolSpecParityTests(unittest.TestCase):
    def test_every_shipped_mcp_server_has_toolspec_or_is_allowlisted(self) -> None:
        shipped = _servers_shipped_on_disk()
        routed = _servers_with_toolspec_routing()

        dark = shipped - routed - _ALLOWLISTED_CALL_MCP_TOOL_ONLY_SERVERS
        self.assertEqual(
            dark,
            set(),
            "New MCP server(s) found under mcp/ with no ToolSpec routing "
            f"and no allow-list entry: {sorted(dark)}. Either give it a "
            "ToolSpec built via _make_mcp_routed_handler(...), or add it "
            "to _ALLOWLISTED_CALL_MCP_TOOL_ONLY_SERVERS in this file with "
            "a reason (see the module docstring).",
        )

    def test_allowlist_has_no_stale_or_now_wired_entries(self) -> None:
        """Keep the allow-list honest so "shrink it" is enforced, not just
        a comment. An entry is stale if its server directory disappeared;
        it is "now wired" if a ToolSpec has since been given to it -- both
        cases mean the entry must be deleted from the allow-list.
        """

        shipped = _servers_shipped_on_disk()
        routed = _servers_with_toolspec_routing()

        removed = _ALLOWLISTED_CALL_MCP_TOOL_ONLY_SERVERS - shipped
        self.assertEqual(
            removed,
            set(),
            "Allow-list names server(s) no longer shipped under mcp/: "
            f"{sorted(removed)}. Delete them from "
            "_ALLOWLISTED_CALL_MCP_TOOL_ONLY_SERVERS.",
        )

        now_wired = _ALLOWLISTED_CALL_MCP_TOOL_ONLY_SERVERS & routed
        self.assertEqual(
            now_wired,
            set(),
            "Allow-list still names server(s) that now have ToolSpec "
            f"routing: {sorted(now_wired)}. Remove them from "
            "_ALLOWLISTED_CALL_MCP_TOOL_ONLY_SERVERS -- shrinking this "
            "list is the goal.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
