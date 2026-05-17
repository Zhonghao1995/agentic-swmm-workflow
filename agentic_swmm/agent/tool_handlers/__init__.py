"""Skill-family-organized tool handlers for the agent runtime (PRD #128).

``agentic_swmm/agent/tool_registry.py`` registers the ToolSpec table and
owns the dispatch/validation shape. The actual per-tool handler bodies
move into focused submodules under this package, one file per skill
family, so:

- a maintainer changing the plot tool opens a ~100 LOC ``swmm_plot.py``,
  not a 2k+ LOC monolith,
- adding a new SWMM-skill tool becomes a one-skill-one-module pattern,
- AI agents navigating the codebase get module sizes that fit their
  context window comfortably.

The submodules import the cross-cutting helpers (``_failure``,
``_strip_html``, MCP-routing factory) from ``tool_registry`` directly
during this migration. A future cleanup PR will move those helpers to
``tool_handlers/_shared.py`` once all families have been extracted.
"""
