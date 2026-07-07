"""Issue #235 Part 4c: skills/swmm-end-to-end/SKILL.md tool names must not rot.

Background
----------
``skills/swmm-end-to-end/SKILL.md`` documents the end-to-end workflow by
naming concrete MCP tools, shaped like ``swmm-<x>-mcp.<tool>`` (e.g.
``swmm-network-mcp.qa``, ``swmm-runner-mcp.swmm_run``). These are plain
prose strings -- nothing type-checks them against the Node MCP servers
under ``mcp/`` that actually declare the tools. A tool can be renamed or
deleted in ``mcp/<server>/server.js`` and the doc keeps citing the old
name; a previous incident left a reference to a deleted
``swmm_parameter_scout`` tool sitting in the skill doc for weeks before
anyone noticed, because nothing ran at CI time to catch it.

This test parses every ``swmm-<x>-mcp.<tool>`` reference out of the
skill doc and asserts the tool name is actually declared in that
server's ``mcp/<x>/server.js``.

Ground truth and check strength
--------------------------------
The Node servers under ``mcp/`` register tools as JS string literals --
either the high-level ``server.tool('name', ...)`` SDK call, or the
low-level ``{ name: 'name', description, inputSchema }`` object used by
servers like ``swmm-runner``/``swmm-gis`` (see
``agentic_swmm/agent/mcp_coverage.py:parse_server_tools`` for a
stricter parser that distinguishes the two styles, built for a
different purpose: proving a ToolSpec's *expected* MCP tool name
matches). This test deliberately does NOT reuse that stricter parser --
grep-level containment (does the quoted tool-name literal appear
anywhere in ``server.js``?) is the right strength here, because the
failure mode being guarded against is a *stale reference to a name that
plainly does not exist any more* in that file, not a subtle schema
mismatch.

Discovery (2026-07-07): as of this writing every one of the 27 unique
``swmm-<x>-mcp.<tool>`` references in the skill doc (46 occurrences
counting repeats) resolves cleanly against its server's ``server.js`` --
the ``swmm_parameter_scout`` incident described above has already been
cleaned up, so no allow-list of known-stale references is seeded here.
If this test ever needs one, add it to ``_KNOWN_STALE_REFERENCES`` below
with a comment naming the tracking issue -- never silently.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "skills" / "swmm-end-to-end" / "SKILL.md"
MCP_ROOT = REPO_ROOT / "mcp"


# ``swmm-network-mcp.qa`` -> ("swmm-network", "qa"). The part before
# ``-mcp.`` is the mcp/<server> directory name; the part after is the
# tool name that server's server.js must declare.
_TOOL_REF_RE = re.compile(r"\b(swmm-[a-z][a-z0-9-]*)-mcp\.([A-Za-z_][A-Za-z0-9_]*)\b")


# Known-stale (server_dir, tool) references allow-listed pending a doc
# fix. Empty today (see module docstring) -- keep it that way. Any
# addition must carry a trailing comment naming the tracking issue.
_KNOWN_STALE_REFERENCES: frozenset[tuple[str, str]] = frozenset()


def _extract_tool_references(text: str) -> set[tuple[str, str]]:
    return set(_TOOL_REF_RE.findall(text))


def _tool_is_declared(server_js: Path, tool: str) -> bool:
    """Grep-level containment: is ``tool`` quoted anywhere in the source?"""

    if not server_js.is_file():
        return False
    text = server_js.read_text(encoding="utf-8")
    return f'"{tool}"' in text or f"'{tool}'" in text


class McpToolNameContractTests(unittest.TestCase):
    def test_skill_md_tool_references_exist_in_named_server_js(self) -> None:
        self.assertTrue(
            SKILL_MD.is_file(),
            f"expected skill doc at {SKILL_MD}; update this test's path if it moved",
        )
        text = SKILL_MD.read_text(encoding="utf-8")
        references = _extract_tool_references(text)
        self.assertTrue(
            references,
            "no swmm-<x>-mcp.<tool> references matched in SKILL.md -- "
            "the doc changed shape or the regex above needs updating",
        )

        stale: list[str] = []
        for server_dir, tool in sorted(references - _KNOWN_STALE_REFERENCES):
            server_js = MCP_ROOT / server_dir / "server.js"
            if not server_js.is_file():
                stale.append(
                    f"{server_dir}-mcp.{tool}: referenced server file "
                    f"does not exist: {server_js}"
                )
            elif not _tool_is_declared(server_js, tool):
                stale.append(
                    f"{server_dir}-mcp.{tool}: tool name not found in {server_js}"
                )

        self.assertEqual(
            stale,
            [],
            f"{SKILL_MD} references MCP tool name(s) that don't exist in "
            "the named server's source:\n" + "\n".join(f"  - {line}" for line in stale),
        )

    def test_known_stale_allowlist_entries_are_still_actually_stale(self) -> None:
        """If ``_KNOWN_STALE_REFERENCES`` is ever populated, keep it
        honest: an entry that stops being stale (doc fixed, or the tool
        now exists) must be deleted, not left to rot as a silent
        allow-list -- the exact failure mode this file exists to prevent.
        """

        if not _KNOWN_STALE_REFERENCES:
            self.skipTest("no known-stale references allow-listed")

        text = SKILL_MD.read_text(encoding="utf-8")
        references = _extract_tool_references(text)
        for server_dir, tool in _KNOWN_STALE_REFERENCES:
            server_js = MCP_ROOT / server_dir / "server.js"
            still_stale = (server_dir, tool) in references and not _tool_is_declared(
                server_js, tool
            )
            self.assertTrue(
                still_stale,
                f"{server_dir}-mcp.{tool} is allow-listed as known-stale "
                "but is no longer stale -- remove it from "
                "_KNOWN_STALE_REFERENCES",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
