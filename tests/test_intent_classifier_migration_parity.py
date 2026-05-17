"""Migration parity for PRD #121.

Each migrated function (``compute_intent_signals``, ``is_open_shaped_prompt``,
``_looks_like_run_continuation``, ``continuation_classifier.classify``,
``planner._is_negated``) keeps its public signature post-migration.
This test parametrises across a representative corpus and asserts the
post-migration value matches a snapshot of the pre-migration behaviour.

If you change a vocabulary table in ``intent_classifier`` and one of
these assertions trips, that is the alarm: a previously-stable signal
just shifted. Update the snapshot only if the shift is intentional.
"""

from __future__ import annotations

import unittest

from agentic_swmm.agent.continuation_classifier import ExecutionPath, classify
from agentic_swmm.agent.planner import _is_negated
from agentic_swmm.agent.runtime_loop import is_open_shaped_prompt
from agentic_swmm.agent.tool_registry import compute_intent_signals


class ComputeIntentSignalsParityTests(unittest.TestCase):
    def test_legacy_dict_shape_unchanged(self) -> None:
        """Six legacy keys plus boolean values — no shape drift."""
        signals = compute_intent_signals("plot the figure")
        expected_keys = {
            "wants_calibration",
            "wants_uncertainty",
            "wants_audit",
            "wants_plot",
            "wants_demo",
            "wants_run",
        }
        self.assertEqual(set(signals.keys()), expected_keys)
        for value in signals.values():
            self.assertIsInstance(value, bool)

    def test_specific_corpus(self) -> None:
        cases = [
            ("plot the figure", {"wants_plot": True}),
            ("calibrate against NSE", {"wants_calibration": True}),
            ("跑校准看看 NSE", {"wants_run": True, "wants_calibration": True}),
            ("做率定", {"wants_calibration": True}),
            ("做不确定性分析", {"wants_uncertainty": True}),
            ("演示一下", {"wants_demo": True}),
            ("hello", {}),
        ]
        for goal, expected_true in cases:
            with self.subTest(goal=goal):
                signals = compute_intent_signals(goal)
                for key, expected in expected_true.items():
                    self.assertEqual(
                        signals[key], expected, f"goal={goal!r} key={key}"
                    )


class IsOpenShapedPromptParityTests(unittest.TestCase):
    def test_greetings_are_open_shaped(self) -> None:
        for prompt in ("hi", "Hello", "你好", "您好", "what can you do"):
            with self.subTest(prompt=prompt):
                self.assertTrue(is_open_shaped_prompt(prompt))

    def test_task_prompts_are_not_open_shaped(self) -> None:
        for prompt in (
            "run swmm",
            "build the inp",
            "plot the figure",
            "跑 tecnopolo demo",
            "Build a SWMM model for Tod Creek",
        ):
            with self.subTest(prompt=prompt):
                self.assertFalse(is_open_shaped_prompt(prompt))

    def test_empty_or_whitespace_is_open_shaped(self) -> None:
        self.assertTrue(is_open_shaped_prompt(""))
        self.assertTrue(is_open_shaped_prompt("   "))


class ContinuationClassifyParityTests(unittest.TestCase):
    """``continuation_classifier.classify`` keeps its enum-mapping shape."""

    def test_active_run_with_plot_vocab_returns_plot_continuation(self) -> None:
        self.assertEqual(
            classify("plot the figure", {"active_run_dir": "/tmp/x"}),
            ExecutionPath.PLOT_CONTINUATION,
        )

    def test_build_intent_always_returns_new_swmm_run(self) -> None:
        self.assertEqual(
            classify("build a new model", {"active_run_dir": "/tmp/x"}),
            ExecutionPath.NEW_SWMM_RUN,
        )

    def test_explicit_inp_no_active_run_returns_new_swmm_run(self) -> None:
        self.assertEqual(
            classify("run examples/foo.inp", None), ExecutionPath.NEW_SWMM_RUN
        )

    def test_empty_prompt_returns_unclear(self) -> None:
        self.assertEqual(classify("", None), ExecutionPath.UNCLEAR)
        self.assertEqual(classify("  ", None), ExecutionPath.UNCLEAR)

    def test_non_string_returns_unclear(self) -> None:
        self.assertEqual(classify(None, None), ExecutionPath.UNCLEAR)  # type: ignore[arg-type]


class NegationParityTests(unittest.TestCase):
    """``planner._is_negated`` delegates to ``intent_classifier.is_negated``."""

    def test_recognised_negation_markers(self) -> None:
        self.assertTrue(_is_negated("我不要 peak 流量", "peak"))
        self.assertTrue(_is_negated("not peak please", "peak"))
        self.assertTrue(_is_negated("别画 peak", "peak"))
        self.assertTrue(_is_negated("no peak", "peak"))

    def test_not_negated_when_no_marker(self) -> None:
        self.assertFalse(_is_negated("please plot peak", "peak"))

    def test_term_absent_returns_false(self) -> None:
        self.assertFalse(_is_negated("some other text", "peak"))


if __name__ == "__main__":
    unittest.main()
