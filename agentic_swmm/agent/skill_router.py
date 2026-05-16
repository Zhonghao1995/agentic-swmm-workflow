"""Skill-as-dispatch-node router (PRD-Y).

Groups the flat ``AgentToolRegistry`` ToolSpec list into a two-level
surface: skill name → tool subset. The planner uses this to first
commit to a workflow skill (``select_skill``) and then choose one of
that skill's concrete tools.

The mapping is deliberately a static table — the source of truth for
"which tool belongs to which skill" lives here, not on the ``ToolSpec``
itself. Adding a new deterministic-SWMM ToolSpec means adding a row in
``_DETERMINISTIC_BINDINGS`` (mirrors ``mcp_coverage.EXPECTED_BINDINGS``).
Adding a new agent-internal tool just means it falls through to the
``agent-internal`` virtual skill.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolSpec


# ---------------------------------------------------------------------------
# Static mapping: ToolSpec name -> skill name. Mirror of
# ``mcp_coverage.EXPECTED_BINDINGS``; kept here as a small table so that
# changes to one source need a matching change to the other (test in
# ``tests/test_skill_router.py`` cross-checks both).
# ---------------------------------------------------------------------------


_DETERMINISTIC_BINDINGS: dict[str, str] = {
    "audit_run": "swmm-experiment-audit",
    "build_inp": "swmm-builder",
    "format_rainfall": "swmm-climate",
    "network_qa": "swmm-network",
    "network_to_inp": "swmm-network",
    "plot_run": "swmm-plot",
    "run_swmm_inp": "swmm-runner",
    "summarize_memory": "swmm-modeling-memory",
}


# The virtual ``agent-internal`` skill. Anything not in
# ``_DETERMINISTIC_BINDINGS`` rolls up here so the planner sees a single
# bucket for in-process introspection / memory / patch tools and the
# new ``select_skill`` meta-tool itself.
AGENT_INTERNAL_SKILL = "agent-internal"


@dataclass(frozen=True)
class SkillTools:
    """One skill's slice of the registry.

    ``source`` reads ``"mcp"`` for deterministic-SWMM skills (handler
    routes through ``MCPPool``) and ``"in-process"`` for the virtual
    agent-internal skill. The string is purely for transparency in the
    planner trace — no behaviour depends on it.
    """

    skill_name: str
    tools: tuple[ToolSpec, ...]
    source: str  # "mcp" | "in-process"

    def tool_names(self) -> list[str]:
        return [tool.name for tool in self.tools]

    def schemas(self) -> list[dict[str, object]]:
        return [tool.schema() for tool in self.tools]


class SkillRouter:
    """Two-level routing over an ``AgentToolRegistry``.

    The router is read-only: it inspects the existing registry and does
    not mutate it. Construction is cheap — no MCP I/O happens here.
    """

    def __init__(self, registry: AgentToolRegistry) -> None:
        self._registry = registry
        self._by_skill: dict[str, list[ToolSpec]] = {}
        self._build_buckets()

    # -- public API -------------------------------------------------------

    def list_skills(self) -> list[str]:
        """Return the sorted set of skills the planner can ``select``.

        Always includes ``agent-internal`` so the planner can request
        the always-available subset without committing to a workflow
        skill.
        """

        return sorted(self._by_skill)

    def tools_for(self, skill_name: str) -> SkillTools:
        """Return the tool subset for ``skill_name``.

        Raises ``KeyError`` on unknown skills so callers can convert
        that to a fail-soft tool result; the planner's ``select_skill``
        handler does that wrapping.
        """

        tools = self._by_skill.get(skill_name)
        if tools is None:
            raise KeyError(skill_name)
        source = "in-process" if skill_name == AGENT_INTERNAL_SKILL else "mcp"
        return SkillTools(skill_name=skill_name, tools=tuple(tools), source=source)

    def virtual_agent_internal_skill(self) -> SkillTools:
        """Convenience: the always-available in-process bucket."""

        return self.tools_for(AGENT_INTERNAL_SKILL)

    # -- internals --------------------------------------------------------

    def _build_buckets(self) -> None:
        # ``_tools`` is the private mapping inside ``AgentToolRegistry``;
        # we touch it deliberately because the public ``schemas()`` /
        # ``sorted_names()`` accessors throw away the ``ToolSpec`` object
        # that we need here for descriptions + parameters.
        all_specs: dict[str, ToolSpec] = getattr(self._registry, "_tools", {})
        for name, spec in sorted(all_specs.items()):
            skill = _DETERMINISTIC_BINDINGS.get(name, AGENT_INTERNAL_SKILL)
            self._by_skill.setdefault(skill, []).append(spec)
        # Even when zero tools currently map to ``agent-internal`` we
        # still expose the virtual skill so ``list_skills`` is stable.
        self._by_skill.setdefault(AGENT_INTERNAL_SKILL, [])
        # Issue #113: also surface every on-disk skill under ``skills/``,
        # even when it has no deterministic-SWMM tool binding. Pure
        # orchestration / contract skills like ``swmm-end-to-end`` ship
        # only a ``SKILL.md`` and rely on the agent reading that file —
        # but the planner must still be able to ``select_skill`` them.
        # An empty tool bucket is the correct representation: the
        # planner falls back to ``agent-internal`` tools (e.g.
        # ``read_skill``) while honouring the chosen skill's contract.
        for name in _on_disk_skill_names():
            self._by_skill.setdefault(name, [])


def _on_disk_skill_names() -> list[str]:
    """Return every skill name from ``skills/<name>/SKILL.md`` on disk.

    Lazy-imports ``runtime.registry`` so this module's import graph
    stays small. Returns an empty list (rather than raising) when the
    skills directory is absent, so unit tests with a stripped-down
    resource root continue to work.
    """

    try:
        from agentic_swmm.runtime.registry import discover_skills

        return [record["name"] for record in discover_skills()]
    except Exception:  # pragma: no cover - defensive
        return []


__all__ = [
    "AGENT_INTERNAL_SKILL",
    "SkillRouter",
    "SkillTools",
]
