"""One parametrized contract over the authoritative binding table (ADR-0006 D2).

Before this file, every PR batch that added tools grew its own ~300-line
"reachability" test file re-proving the same five facts per tool
(registered / routed / listed / bucketed / script exists), and the same
tool->skill fact lived in THREE hand-maintained tables that could drift
(the map_run mis-bucketing was a real casualty). Now:

* ``mcp_coverage.EXPECTED_BINDINGS`` is the single authoritative map;
  ``skill_router._DETERMINISTIC_BINDINGS`` and the handler-lockin test
  DERIVE from it,
* this file proves the generic facts for EVERY row, forever, so a new
  tool needs its behaviour tests only, never another reachability file.
"""
from __future__ import annotations

import pytest

from agentic_swmm.agent.mcp_coverage import EXPECTED_BINDINGS
from agentic_swmm.agent.skill_router import (
    _DETERMINISTIC_BINDINGS,
    _DIRECT_SUBPROCESS_BINDINGS,
)
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.utils.paths import repo_root


@pytest.fixture(scope="module")
def registry() -> AgentToolRegistry:
    return AgentToolRegistry()


@pytest.mark.parametrize("binding", EXPECTED_BINDINGS, ids=lambda b: b.tool_spec_name)
class TestEveryMcpRoutedBinding:
    def test_tool_is_registered(self, binding, registry) -> None:
        assert binding.tool_spec_name in registry.names

    def test_tool_routes_to_its_server(self, binding, registry) -> None:
        routing = registry.mcp_routing(binding.tool_spec_name)
        assert routing == {
            "server": binding.mcp_server,
            "tool": binding.mcp_tool_name,
        }, "mcp_routing() must agree with EXPECTED_BINDINGS"

    def test_script_exists(self, binding) -> None:
        assert (repo_root() / binding.script_relpath).is_file()

    def test_router_buckets_tool_under_its_skill(self, binding) -> None:
        assert _DETERMINISTIC_BINDINGS[binding.tool_spec_name] == binding.mcp_server

    def test_server_dir_exists(self, binding) -> None:
        assert (repo_root() / "mcp" / binding.mcp_server / "server.js").is_file()


@pytest.mark.parametrize(
    "tool,skill", sorted(_DIRECT_SUBPROCESS_BINDINGS.items()), ids=lambda x: str(x)
)
def test_direct_subprocess_supplement_rows(tool, skill, registry) -> None:
    """Supplement rows are registered tools with NO MCP routing (that is
    what makes them supplement rows) and a real skill folder."""
    assert tool in registry.names
    assert registry.mcp_routing(tool) is None
    assert (repo_root() / "skills" / skill / "SKILL.md").is_file()


def test_supplement_and_derived_sets_are_disjoint() -> None:
    derived = {b.tool_spec_name for b in EXPECTED_BINDINGS}
    overlap = derived & set(_DIRECT_SUBPROCESS_BINDINGS)
    assert not overlap, f"tool(s) in both tables: {overlap}"


def test_map_run_is_visible_under_swmm_plot() -> None:
    """The ADR-0006 casualty, pinned: select_skill('swmm-plot') must list
    map_run alongside plot_run."""
    from agentic_swmm.agent.skill_router import SkillRouter

    router = SkillRouter(AgentToolRegistry())
    names = router.tools_for("swmm-plot").tool_names()
    assert "plot_run" in names and "map_run" in names
