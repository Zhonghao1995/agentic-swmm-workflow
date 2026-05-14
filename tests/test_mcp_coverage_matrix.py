"""Lock-in test: every subprocess-Python ToolSpec has a matching MCP tool.

Per PRD-X User Stories 10 + 11 and the Done Criteria
"``aiswmm mcp coverage`` exits 0", this test mirrors the coverage logic in
pure Python. If a future PR adds a ToolSpec whose handler subprocess-calls
a ``skills/<skill>/scripts/<x>.py`` script without adding the matching
``server.tool(...)`` entry in ``mcp/<skill>/server.js``, this gate fails.

We deliberately keep the expected mapping explicit (rather than reflectively
introspecting handler bodies) because:
- handler closures route through ``aiswmm <subcommand>`` indirection
  (``audit_run`` -> ``aiswmm audit`` -> ``skills/swmm-experiment-audit``),
- explicit data is easier to review in PRs than dynamic shell heuristics,
- the table doubles as documentation for which MCP tool to call.
"""

from __future__ import annotations

from agentic_swmm.agent.mcp_coverage import build_coverage_matrix


def test_every_subprocess_toolspec_has_matching_mcp_tool() -> None:
    matrix = build_coverage_matrix()
    missing = [row for row in matrix if row.status != "OK"]
    if missing:
        details = "\n".join(
            f"  {row.tool_spec_name}: script={row.script_relpath} -> "
            f"expected {row.mcp_server}.{row.mcp_tool_name} ({row.status})"
            for row in missing
        )
        raise AssertionError(
            "MCP coverage gate: some subprocess-Python ToolSpecs lack a "
            "matching MCP server.tool(...) registration:\n" + details
        )


def test_coverage_matrix_is_not_empty() -> None:
    """Sanity: if the matrix is empty, the gate above passes vacuously."""

    matrix = build_coverage_matrix()
    assert len(matrix) >= 5, "expected at least 5 subprocess-Python ToolSpecs"
