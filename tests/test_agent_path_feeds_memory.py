"""The golden path feeds the memory it is meant to learn from (F-35, F-21, F-33).

2026-09-02 live campaign: four real interactive sessions audited their runs
and left zero parametric rows, because only the CLI verb ever called the
audit -> memory hook; and the planner anchored memory on a 1-token sniff
that a place-name prompt never satisfies. (The third finding, a lessons
re-render that dropped a curated pattern, went away with the lessons
file in the memory simplification, 2026-09-26.)
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.tool_handlers import swmm_audit
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root

