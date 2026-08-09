"""Regression tests: apply_patch understands the OpenAI patch envelope.

Bug (found live 2026-08-09, NL sweep on the codex route): GPT-family
planners natively emit the patch-envelope format (``*** Begin Patch`` /
``*** Add File:`` ...), but ``_patch_paths`` only parsed unified-diff
headers and the applier was ``git apply``. Every envelope attempt
failed with the misleading "patch did not contain recognizable file
paths", so the planner could never write or edit a file on the default
route.

Fix under test: envelope paths are extracted (so the evidence and
policy guards fire honestly), and an in-process engine applies
Add/Update/Delete File sections with exact-context matching that fails
loudly on ambiguity. Unified diffs keep flowing through ``git apply``.
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from agentic_swmm.agent.tool_handlers.runtime_ops import (
    _apply_patch_tool,
    _patch_paths,
)
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root


_SCRATCH = repo_root() / "runs" / "_test_envelope"


def setUpModule() -> None:  # pragma: no cover - fixture
    if _SCRATCH.exists():
        shutil.rmtree(_SCRATCH, ignore_errors=True)
    _SCRATCH.mkdir(parents=True)


def tearDownModule() -> None:  # pragma: no cover - fixture
    if _SCRATCH.exists():
        shutil.rmtree(_SCRATCH, ignore_errors=True)


def _call(patch: str, *, allow_evidence: bool = True) -> dict:
    call = ToolCall(
        name="apply_patch",
        args={"patch": patch, "allow_evidence_edits": allow_evidence},
    )
    return _apply_patch_tool(call, _SCRATCH)


def _rel(path: Path) -> str:
    return str(path.relative_to(repo_root()))


class EnvelopePathExtractionTests(unittest.TestCase):
    def test_envelope_headers_are_recognized(self) -> None:
        patch = (
            "*** Begin Patch\n"
            "*** Add File: runs/_test_envelope/a.json\n"
            "+{}\n"
            "*** Update File: runs/_test_envelope/b.txt\n"
            " ctx\n"
            "*** Delete File: runs/_test_envelope/c.txt\n"
            "*** End Patch"
        )
        self.assertEqual(
            _patch_paths(patch),
            [
                "runs/_test_envelope/a.json",
                "runs/_test_envelope/b.txt",
                "runs/_test_envelope/c.txt",
            ],
        )

    def test_unified_diff_headers_still_recognized(self) -> None:
        patch = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        self.assertEqual(_patch_paths(patch), ["x.py"])


class EnvelopeApplyTests(unittest.TestCase):
    def test_add_file_creates_content(self) -> None:
        target = _SCRATCH / "cfg" / "space.json"
        patch = (
            "*** Begin Patch\n"
            f"*** Add File: {_rel(target)}\n"
            "+{\n"
            '+  "manning_n": {"min": 0.01, "max": 0.03}\n'
            "+}\n"
            "*** End Patch"
        )
        result = _call(patch)
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            '{\n  "manning_n": {"min": 0.01, "max": 0.03}\n}\n',
        )

    def test_update_file_applies_context_hunk(self) -> None:
        target = _SCRATCH / "u.txt"
        target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {_rel(target)}\n"
            " alpha\n"
            "-beta\n"
            "+BETA\n"
            " gamma\n"
            "*** End Patch"
        )
        result = _call(patch)
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            target.read_text(encoding="utf-8"), "alpha\nBETA\ngamma\n"
        )

    def test_ambiguous_context_fails_loudly(self) -> None:
        target = _SCRATCH / "amb.txt"
        target.write_text("x\nsame\nx\nsame\n", encoding="utf-8")
        patch = (
            "*** Begin Patch\n"
            f"*** Update File: {_rel(target)}\n"
            "-same\n"
            "+different\n"
            "*** End Patch"
        )
        result = _call(patch)
        self.assertFalse(result["ok"])
        self.assertIn("matched 2 location(s)", result["summary"])
        # Failed patch must leave the file untouched.
        self.assertEqual(target.read_text(encoding="utf-8"), "x\nsame\nx\nsame\n")

    def test_delete_file(self) -> None:
        target = _SCRATCH / "gone.txt"
        target.write_text("bye\n", encoding="utf-8")
        patch = (
            "*** Begin Patch\n"
            f"*** Delete File: {_rel(target)}\n"
            "*** End Patch"
        )
        result = _call(patch)
        self.assertTrue(result["ok"], result)
        self.assertFalse(target.exists())

    def test_evidence_guard_fires_with_honest_message(self) -> None:
        """The live failure now yields the evidence-guard explanation,
        not the old 'no recognizable file paths' lie."""
        patch = (
            "*** Begin Patch\n"
            "*** Add File: runs/_test_envelope/guarded.json\n"
            "+{}\n"
            "*** End Patch"
        )
        result = _call(patch, allow_evidence=False)
        self.assertFalse(result["ok"])
        self.assertIn("evidence", result["summary"])
        self.assertNotIn("recognizable", result["summary"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
