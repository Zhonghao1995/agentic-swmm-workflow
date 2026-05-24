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

Cross-cutting helpers (``_failure``, ``_repo_path``, ``_run_cli_tool``,
``_strip_html``, ...) live in :mod:`._shared`. Both ``tool_registry``
and the family submodules import them from there so there is exactly
one source of truth for the canonical failure shape, repo-sandbox path
check, and subprocess wrapper.
"""
