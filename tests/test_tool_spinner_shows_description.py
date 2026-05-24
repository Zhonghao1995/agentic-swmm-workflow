"""Tool spinner shows the first sentence of the tool's description.

Issue #58 (UX-3) replaces the plain ``[i/N] toolname`` line emitted by
the agent loop with ``Running <toolname> — <first sentence of
description>`` while the tool is running, then ``✓`` / ``✗`` with
elapsed time on finish.

PRD-185 makes that descriptive label opt-in via ``--verbose`` so the
new default (digest) keeps the spinner row terse. This test moves
under the ``--verbose`` umbrella by constructing the executor with
``verbose=True``; the digest-mode counterpart lives in
``tests/test_executor_digest_spinner_label.py``.
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
    """Use the real registry so ``ToolSpec.description`` lookup works,
    but short-circuit ``execute`` so we don't actually shell out.
    """

    def execute(self, call: ToolCall, session_dir: Path) -> dict[str, Any]:
        return {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}


class ToolSpinnerShowsDescriptionTests(unittest.TestCase):
    def test_tool_spinner_for_build_inp_contains_name_and_first_sentence(self) -> None:
        registry = _CannedRegistry()
        # Look up the live description so the test stays in lock-step
        # with whatever registry.py declares — we only pin the
        # first-sentence rule, not the wording.
        description = registry._tools["build_inp"].description  # noqa: SLF001 - test
        first_sentence = description.split(".")[0].strip()
        # Sanity check the registry actually exposes a non-trivial
        # description for build_inp.
        self.assertTrue(
            len(first_sentence) > 5,
            f"build_inp description too short: {description!r}",
        )

        stream = _FakeTTYStream()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            executor = AgentExecutor(
                registry,
                session_dir=session_dir,
                trace_path=session_dir / "agent_trace.jsonl",
                profile=Profile.QUICK,
                progress_stream=stream,
                verbose=True,
            )
            executor.execute(
                ToolCall(
                    "build_inp",
                    {
                        "subcatchments_csv": "x",
                        "params_json": "y",
                        "network_json": "z",
                        "out_inp": "out.inp",
                        "out_manifest": "manifest.json",
                    },
                ),
                index=1,
            )
            executor.close()

        output = stream.getvalue()
        self.assertIn(
            "build_inp",
            output,
            f"spinner must include the tool name; got {output!r}",
        )
        self.assertIn(
            first_sentence,
            output,
            f"spinner must include the first sentence of the description; "
            f"expected {first_sentence!r} in {output!r}",
        )


if __name__ == "__main__":
    unittest.main()
