"""PRD-185: executor spinner label drops the description in digest mode.

In ``--verbose`` mode the per-tool spinner keeps the legacy
``Running <name> — <first sentence of description>`` text (so the
debugging path is byte-identical). In digest mode (the new default)
the description is dropped and the spinner shows only the bare tool
name, matching the PRD's "Drops" list:

    Tool description text ("Run a repository or imported external...")

The executor exposes the toggle via the ``verbose`` ctor flag; the
runtime loop is responsible for plumbing it from ``args.verbose``.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


class _FakeTTYStream(io.StringIO):
    def isatty(self) -> bool:  # type: ignore[override]
        return True


class _CannedRegistry(AgentToolRegistry):
    def execute(self, call: ToolCall, session_dir: Path) -> dict[str, Any]:
        return {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}


class ExecutorDigestSpinnerLabelTests(unittest.TestCase):
    def _run_one_tool(self, *, verbose: bool) -> str:
        registry = _CannedRegistry()
        stream = _FakeTTYStream()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            executor = AgentExecutor(
                registry,
                session_dir=session_dir,
                trace_path=session_dir / "agent_trace.jsonl",
                profile=Profile.QUICK,
                progress_stream=stream,
                verbose=verbose,
            )
            executor.execute(ToolCall("list_skills", {}), index=1)
            executor.close()
        return stream.getvalue()

    def test_digest_mode_spinner_shows_only_tool_name(self) -> None:
        output = self._run_one_tool(verbose=False)
        # Spinner still includes the tool name so the user sees motion.
        self.assertIn("list_skills", output)
        # But the description text is gone — no "List available" anywhere.
        # (We pull the live description fragment from the registry to
        # stay coupled to the actual wording instead of hard-coding it.)
        live_description = AgentToolRegistry()._tools["list_skills"].description  # noqa: SLF001
        first_sentence = live_description.split(".")[0].strip()
        self.assertNotIn(
            first_sentence,
            output,
            f"digest-mode spinner must drop the description; "
            f"found {first_sentence!r} in {output!r}",
        )
        # And the "Running <name> — " preamble (the marker that the
        # description block is present) must NOT appear.
        self.assertNotIn(" — ", output)

    def test_verbose_mode_spinner_preserves_description(self) -> None:
        # Sanity that --verbose still gives the legacy spinner text
        # for the same tool — the digest mode change must be a
        # one-flag delta, not a regression on the debug path.
        output = self._run_one_tool(verbose=True)
        self.assertIn("list_skills", output)
        live_description = AgentToolRegistry()._tools["list_skills"].description  # noqa: SLF001
        first_sentence = live_description.split(".")[0].strip()
        self.assertIn(first_sentence, output)
        self.assertIn(" — ", output)


if __name__ == "__main__":
    unittest.main()
