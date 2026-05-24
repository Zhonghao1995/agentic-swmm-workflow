"""PRD-185: session-end digest summary block in run_openai_planner.

After the planner finishes a SWMM-running turn, the runtime loop in
digest mode prints a 3-4 line block listing peak / continuity / run
dir for every manifest.json the session produced. ``--verbose`` keeps
the current retro-chrome result card path untouched.

The test patches the planner runner so we exercise only the
runtime_loop's session-end rendering — no real OpenAI provider, no
real SWMM run.
"""
from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.planner import PlannerRun
from agentic_swmm.agent.tool_registry import AgentToolRegistry


_MANIFEST_OK = {
    "return_code": 0,
    "metrics": {
        "peak": {"node": "OUT_0", "peak": 0.061, "time_hhmm": "03:15"},
        "continuity": {
            "runoff_quantity": {"Continuity Error (%)": -0.13},
            "flow_routing": {"Continuity Error (%)": -0.004},
        },
    },
}


def _stub_args(*, verbose: bool, session_dir: Path | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        planner="openai",
        provider="openai",
        model="gpt-test",
        session_id=None,
        session_dir=session_dir,
        dry_run=False,
        interactive=False,
        max_steps=4,
        verbose=verbose,
        safe=False,
        quick=False,
        goal=[],
        example=None,
    )


def _make_run_outcome() -> PlannerRun:
    return PlannerRun(ok=True, plan=[], results=[], final_text="done")


class RuntimeLoopDigestSummaryTests(unittest.TestCase):
    def _exercise(self, *, verbose: bool) -> str:
        """Run run_openai_planner with a stubbed plan runner and capture stdout."""
        from agentic_swmm.agent import runtime_loop

        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "230510_tecnopolo_run"
            session_dir.mkdir(parents=True)
            (session_dir / "manifest.json").write_text(
                json.dumps(_MANIFEST_OK), encoding="utf-8"
            )

            args = _stub_args(verbose=verbose)
            buf = io.StringIO()
            with redirect_stdout(buf), mock.patch.object(
                runtime_loop,
                "run_openai_plan",
                return_value=_make_run_outcome(),
            ), mock.patch.object(
                runtime_loop, "OpenAIProvider", return_value=mock.MagicMock()
            ), mock.patch.object(
                runtime_loop, "ensure_session_pool"
            ), mock.patch.object(
                runtime_loop, "load_config",
                return_value=mock.MagicMock(get=lambda *_a, **_kw: "openai"),
            ):
                runtime_loop.run_openai_planner(
                    args,
                    goal="run model",
                    session_dir=session_dir,
                    trace_path=session_dir / "agent_trace.jsonl",
                    registry=AgentToolRegistry(),
                )
            return buf.getvalue()

    def test_digest_mode_prints_session_end_summary_block(self) -> None:
        output = self._exercise(verbose=False)
        self.assertIn("Peak: 0.061 CMS @ 03:15 at OUT_0", output)
        self.assertIn(
            "Continuity: runoff -0.13 %, routing -0.004 %", output
        )
        self.assertIn("Run dir: ", output)
        # The separator is rendered just above the summary lines so
        # the block visually detaches from the step rows.
        self.assertTrue(
            "─" in output or "----" in output,
            f"summary block must carry a separator line; got: {output!r}",
        )

    def test_verbose_mode_does_not_print_digest_summary_block(self) -> None:
        # Verbose keeps the retro-chrome result card; the digest
        # summary block is a digest-only addition.
        output = self._exercise(verbose=True)
        self.assertNotIn("Peak: 0.061 CMS @ 03:15 at OUT_0", output)
        self.assertNotIn(
            "Continuity: runoff -0.13 %, routing -0.004 %", output
        )


if __name__ == "__main__":
    unittest.main()
