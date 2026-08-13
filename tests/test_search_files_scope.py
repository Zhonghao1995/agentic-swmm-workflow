"""``search_files`` must not read the whole install to find one line.

Reported from a live session: a natural-language request to run a model in a
new directory sat on `search_files` for 2m25s and read as a hang. It was not
looping. The tool walked the repository with a skip list of
``.git``/``.venv``/``__pycache__`` and read every remaining file in full, and
an install with the 11 MCP servers present puts ~52k node_modules files in
front of the ~11k that can actually hold model content.

Measured on that repository: 78,626 entries walked, 63,579 surviving the old
skip list, against 11,472 worth searching.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.tool_handlers import runtime_ops
from agentic_swmm.agent.types import ToolCall


class SearchScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        patcher = mock.patch.object(runtime_ops, "repo_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, relative: str, body: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def _search(self, query: str, glob: str = "*", **extra):
        call = ToolCall(name="search_files", args={"query": query, "glob": glob, **extra})
        return runtime_ops._search_files_tool(call, self.root)

    def test_finds_a_match_in_project_content(self) -> None:
        self._write("examples/33/22.inp", "[TIMESERIES]\nTEMP_ROME FILE EXT_TEM.dat\n")
        out = self._search("TEMP_ROME", "**/*.inp")
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["path"], "examples/33/22.inp")

    def test_vendored_and_generated_trees_are_skipped(self) -> None:
        self._write("mcp/swmm-runner/node_modules/zod/README.md", "TEMP_ROME\n")
        self._write("build/lib/copy.py", "TEMP_ROME\n")
        self._write("dist/whatever.txt", "TEMP_ROME\n")
        self._write(".venv/lib/site-packages/x.py", "TEMP_ROME\n")
        out = self._search("TEMP_ROME", "**/*")
        self.assertEqual(out["results"], [], "searched a vendored or generated tree")
        self.assertEqual(out["scanned"], 0)

    def test_oversized_files_are_not_read(self) -> None:
        # A model .inp is kilobytes; anything past the cap is a binary or an
        # archive and reading it is pure latency.
        self._write("data/huge.txt", "x" * (runtime_ops._MAX_SEARCH_FILE_BYTES + 1))
        out = self._search("xxx", "**/*")
        self.assertEqual(out["scanned"], 0)

    def test_a_capped_scan_says_it_is_incomplete(self) -> None:
        # Silent truncation is the dangerous shape: "0 match(es)" after a
        # capped scan reads as "the file is not there".
        for index in range(12):
            self._write(f"pad/f{index}.txt", "nothing here\n")
        with mock.patch.object(runtime_ops, "_MAX_SEARCH_SCANNED", 5):
            out = self._search("TEMP_ROME", "**/*")
        self.assertTrue(out["truncated"])
        self.assertIn("incomplete", out["summary"])
        self.assertEqual(out["scanned"], 5)

    def test_an_uncapped_scan_is_not_flagged(self) -> None:
        self._write("examples/33/22.inp", "TEMP_ROME\n")
        out = self._search("TEMP_ROME", "**/*")
        self.assertFalse(out["truncated"])
        self.assertNotIn("incomplete", out["summary"])

    def test_max_results_still_bounds_the_answer(self) -> None:
        for index in range(10):
            self._write(f"examples/f{index}.inp", "TEMP_ROME\n")
        out = self._search("TEMP_ROME", "**/*.inp", max_results=3)
        self.assertEqual(len(out["results"]), 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
