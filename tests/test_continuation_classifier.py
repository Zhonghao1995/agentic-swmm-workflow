"""Unit tests for ``continuation_classifier.classify``.

PRD_runtime "Module: Continuation classifier":

- ``classify(prompt, workflow_state) -> ExecutionPath``
- ``ExecutionPath = Enum{NEW_SWMM_RUN, NEW_CHAT, PLOT_CONTINUATION, UNCLEAR}``
- ``PLOT_CONTINUATION`` when ``active_run_dir`` is set AND the prompt
  matches the plot heuristic (node id or known variable keyword such as
  ``inflow``, ``depth``, ``flow``, ``peak``).
"""
from __future__ import annotations

import unittest

from agentic_swmm.agent.continuation_classifier import ExecutionPath, classify


class ClassifyTests(unittest.TestCase):
    def test_empty_state_chat_prompt_is_new_chat(self) -> None:
        path = classify("what skills do you have?", {})
        self.assertEqual(path, ExecutionPath.NEW_CHAT)

    def test_empty_state_swmm_build_prompt_is_new_swmm_run(self) -> None:
        # "run examples/tecnopolo.inp" is a clear SWMM build/run request
        # with no active run yet.
        path = classify(
            "run examples/swmm-tecnopolo/tecnopolo-199401.inp",
            {},
        )
        self.assertEqual(path, ExecutionPath.NEW_SWMM_RUN)

    def test_active_run_plus_plot_prompt_is_plot_continuation(self) -> None:
        state = {"active_run_dir": "runs/end-to-end/tecnopolo-199401-prepared"}
        path = classify("plot J2 depth", state)
        self.assertEqual(path, ExecutionPath.PLOT_CONTINUATION)

    def test_active_run_plus_ambiguous_prompt_is_unclear(self) -> None:
        # Ambiguous: an active run exists but the prompt has neither
        # plot/build vocabulary nor a node id.
        state = {"active_run_dir": "runs/end-to-end/tecnopolo-199401-prepared"}
        path = classify("what now?", state)
        self.assertEqual(path, ExecutionPath.UNCLEAR)

    def test_active_run_plus_new_build_prompt_is_new_swmm_run(self) -> None:
        # User explicitly asks for a NEW build — overrides the
        # continuation default.
        state = {"active_run_dir": "runs/end-to-end/tecnopolo-199401-prepared"}
        path = classify(
            "build a new SWMM model from examples/todcreek/network.json",
            state,
        )
        self.assertEqual(path, ExecutionPath.NEW_SWMM_RUN)

    def test_bare_audit_is_new_chat(self) -> None:
        # Without an active_run_dir, "/audit" alone does not classify
        # as a SWMM run — it is a chat command requesting an audit.
        path = classify("/audit", {})
        self.assertEqual(path, ExecutionPath.NEW_CHAT)

    def test_chinese_plot_continuation_with_active_run(self) -> None:
        # The Chinese prompt from the PRD's regression test.
        state = {"active_run_dir": "runs/end-to-end/tecnopolo-199401-prepared"}
        path = classify("换成 J2 depth plot", state)
        self.assertEqual(path, ExecutionPath.PLOT_CONTINUATION)

    def test_active_run_plus_node_id_only_is_plot_continuation(self) -> None:
        # Node id with no explicit verb still implies plot continuation
        # when an active run is present.
        state = {"active_run_dir": "runs/end-to-end/tecnopolo-199401-prepared"}
        path = classify("J2 inflow", state)
        self.assertEqual(path, ExecutionPath.PLOT_CONTINUATION)


if __name__ == "__main__":
    unittest.main()
