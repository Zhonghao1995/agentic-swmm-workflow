"""Coverage matrix: ToolSpec -> Python script -> MCP server.tool.

Per PRD-X Decisions, every ``ToolSpec`` whose handler subprocess-calls a
``skills/<skill>/scripts/<x>.py`` script must have a matching MCP tool that
wraps the same script, so PRD-Y can later route those handlers through the
long-running pool.

This module is the single source of truth for:
- ``tests/test_mcp_coverage_matrix.py`` (lock-in: must be all OK)
- ``aiswmm mcp coverage`` (human-facing audit CLI)

The expected mapping is deliberately explicit. Handlers route through
``aiswmm <subcommand>`` indirection (e.g. ``audit_run`` -> ``aiswmm audit``
-> ``skills/swmm-experiment-audit/scripts/audit_run.py``), so reflective
introspection of handler closures would be brittle and hard to review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agentic_swmm.utils.paths import repo_root


@dataclass(frozen=True)
class ExpectedBinding:
    """One row of the audit: a ToolSpec's expected MCP exposure."""

    tool_spec_name: str
    script_relpath: str
    mcp_server: str
    mcp_tool_name: str


# The canonical audit table. Keep this list sorted by ToolSpec name so PRs
# show a stable diff. Adding a new subprocess-Python ToolSpec without
# adding a row here is itself a failure mode worth catching, but the gate
# in this PRD is the inverse direction — every row's mcp_tool_name MUST
# be exposed by the named server.
EXPECTED_BINDINGS: tuple[ExpectedBinding, ...] = (
    ExpectedBinding(
        tool_spec_name="audit_run",
        script_relpath="skills/swmm-experiment-audit/scripts/audit_run.py",
        mcp_server="swmm-experiment-audit",
        mcp_tool_name="audit_run",
    ),
    ExpectedBinding(
        tool_spec_name="build_inp",
        script_relpath="skills/swmm-builder/scripts/build_swmm_inp.py",
        mcp_server="swmm-builder",
        mcp_tool_name="build_inp",
    ),
    ExpectedBinding(
        tool_spec_name="format_rainfall",
        script_relpath="skills/swmm-climate/scripts/format_rainfall.py",
        mcp_server="swmm-climate",
        mcp_tool_name="format_rainfall",
    ),
    ExpectedBinding(
        tool_spec_name="network_qa",
        script_relpath="skills/swmm-network/scripts/network_qa.py",
        mcp_server="swmm-network",
        mcp_tool_name="qa",
    ),
    ExpectedBinding(
        tool_spec_name="network_to_inp",
        script_relpath="skills/swmm-network/scripts/network_to_inp.py",
        mcp_server="swmm-network",
        mcp_tool_name="export_inp",
    ),
    ExpectedBinding(
        tool_spec_name="plot_run",
        script_relpath="skills/swmm-plot/scripts/plot_rain_runoff_si.py",
        mcp_server="swmm-plot",
        mcp_tool_name="plot_rain_runoff_si",
    ),
    ExpectedBinding(
        tool_spec_name="run_swmm_inp",
        script_relpath="skills/swmm-runner/scripts/swmm_runner.py",
        mcp_server="swmm-runner",
        mcp_tool_name="swmm_run",
    ),
    ExpectedBinding(
        tool_spec_name="summarize_memory",
        script_relpath="skills/swmm-modeling-memory/scripts/summarize_memory.py",
        mcp_server="swmm-modeling-memory",
        mcp_tool_name="summarize_memory",
    ),
)


@dataclass(frozen=True)
class CoverageRow:
    tool_spec_name: str
    script_relpath: str
    mcp_server: str
    mcp_tool_name: str
    status: str  # "OK" / "MISSING_TOOL" / "MISSING_SERVER" / "MISSING_SCRIPT"


# Regex that captures both registration styles in our server.js files:
# - ``server.tool('name', ...)`` (high-level SDK API)
# - ``{ name: "name", description: "...", inputSchema: ... }`` blocks inside
#   ``ListToolsRequestSchema`` handlers (low-level SDK API; swmm-runner).
_TOOL_RE = re.compile(
    r"""server\.tool\(\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)
_NAME_RE = re.compile(
    r"""(?:^|[{,])\s*name\s*:\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)
# When the McpServer is constructed with ``{ name: 'swmm-foo-mcp', version: ... }``
# we must not mistake that for a tool name. The construction lives in either
# ``new McpServer(`` or ``new Server(``.
_SERVER_CTOR_RE = re.compile(
    r"""new\s+(?:Mcp)?Server\s*\(\s*\{\s*name\s*:\s*['"]([^'"]+)['"]""",
)


def parse_server_tools(server_js: Path) -> list[str]:
    """Return the list of tool names registered by a server.js file.

    Static parser — does not execute Node. Supports both ``server.tool('x',
    ...)`` and the lower-level ``{ name: 'x', description, inputSchema }``
    declaration used by ``swmm-runner``.
    """

    if not server_js.is_file():
        return []
    src = server_js.read_text(encoding="utf-8")
    tools = list(_TOOL_RE.findall(src))
    server_names = set(_SERVER_CTOR_RE.findall(src))
    for match in _NAME_RE.findall(src):
        if match in server_names:
            continue
        if match in tools:
            continue
        tools.append(match)
    return tools


def build_coverage_matrix(repo: Path | None = None) -> list[CoverageRow]:
    """Walk the expected bindings and report ``OK`` / ``MISSING_*`` per row."""

    root = repo or repo_root()
    rows: list[CoverageRow] = []
    for binding in EXPECTED_BINDINGS:
        script = root / binding.script_relpath
        server_js = root / "mcp" / binding.mcp_server / "server.js"
        if not script.is_file():
            status = "MISSING_SCRIPT"
        elif not server_js.is_file():
            status = "MISSING_SERVER"
        else:
            tools = parse_server_tools(server_js)
            status = "OK" if binding.mcp_tool_name in tools else "MISSING_TOOL"
        rows.append(
            CoverageRow(
                tool_spec_name=binding.tool_spec_name,
                script_relpath=binding.script_relpath,
                mcp_server=binding.mcp_server,
                mcp_tool_name=binding.mcp_tool_name,
                status=status,
            )
        )
    return rows


def format_coverage_table(rows: list[CoverageRow]) -> str:
    """Render the matrix as a human-readable fixed-width table."""

    headers = ("ToolSpec", "MCP server", "MCP tool", "Status")
    data: list[tuple[str, str, str, str]] = [
        (row.tool_spec_name, row.mcp_server, row.mcp_tool_name, row.status)
        for row in rows
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in data) if data else (len(headers[i]),))
        for i in range(4)
    ]
    sep = "  "

    def _fmt(cols: tuple[str, str, str, str]) -> str:
        return sep.join(c.ljust(widths[i]) for i, c in enumerate(cols))

    lines = [_fmt(headers), _fmt(("-" * widths[0], "-" * widths[1], "-" * widths[2], "-" * widths[3]))]
    lines.extend(_fmt(row) for row in data)
    return "\n".join(lines)


__all__ = [
    "CoverageRow",
    "EXPECTED_BINDINGS",
    "ExpectedBinding",
    "build_coverage_matrix",
    "format_coverage_table",
    "parse_server_tools",
]
