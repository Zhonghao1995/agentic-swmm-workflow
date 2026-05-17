"""Tests for ``agentic_swmm.agent.intent_classifier`` (PRD #121).

The classifier is the single source of truth for keyword-driven decisions
the agent runtime makes about a user goal. These tests pin the bilingual
vocabulary, negation handling, and signal shape so future migrations
(more languages, new intents) cannot silently regress behaviour.
"""

from __future__ import annotations

import unittest

from agentic_swmm.agent.intent_classifier import IntentSignals, classify_intent


class IntentSignalsShapeTests(unittest.TestCase):
    """``classify_intent`` returns the documented ``IntentSignals`` shape."""

    def test_returns_intent_signals_dataclass(self) -> None:
        signals = classify_intent("run swmm")
        self.assertIsInstance(signals, IntentSignals)

    def test_signals_expose_bool_wants_run(self) -> None:
        self.assertTrue(classify_intent("run swmm").wants_run)
        self.assertFalse(classify_intent("hello").wants_run)

    def test_signals_expose_bool_wants_plot(self) -> None:
        self.assertTrue(classify_intent("plot the figure").wants_plot)
        self.assertFalse(classify_intent("hello").wants_plot)

    def test_signals_expose_bool_wants_calibration(self) -> None:
        self.assertTrue(classify_intent("calibrate against NSE").wants_calibration)
        self.assertFalse(classify_intent("run swmm").wants_calibration)

    def test_signals_expose_bool_wants_uncertainty(self) -> None:
        self.assertTrue(classify_intent("fuzzy uncertainty analysis").wants_uncertainty)
        self.assertFalse(classify_intent("run swmm").wants_uncertainty)

    def test_signals_expose_bool_wants_audit(self) -> None:
        self.assertTrue(classify_intent("audit the run").wants_audit)
        self.assertTrue(classify_intent("compare the runs").wants_audit)

    def test_signals_expose_bool_wants_demo(self) -> None:
        self.assertTrue(classify_intent("run acceptance demo").wants_demo)

    def test_signals_expose_as_dict(self) -> None:
        """``as_dict`` provides the legacy ``compute_intent_signals`` shape."""
        signals = classify_intent("plot the figure")
        data = signals.as_dict()
        self.assertIsInstance(data, dict)
        for key in (
            "wants_calibration",
            "wants_uncertainty",
            "wants_audit",
            "wants_plot",
            "wants_demo",
            "wants_run",
        ):
            self.assertIn(key, data)


class BilingualVocabularyTests(unittest.TestCase):
    """Every EN signal that fires must have a parallel ZH coverage."""

    def test_wants_run_bilingual(self) -> None:
        self.assertTrue(classify_intent("run the model").wants_run)
        self.assertTrue(classify_intent("运行 SWMM").wants_run)
        self.assertTrue(classify_intent("跑一下 demo").wants_run)

    def test_wants_plot_bilingual(self) -> None:
        self.assertTrue(classify_intent("plot the figure").wants_plot)
        self.assertTrue(classify_intent("作图").wants_plot)
        self.assertTrue(classify_intent("画图").wants_plot)

    def test_wants_audit_bilingual(self) -> None:
        self.assertTrue(classify_intent("audit the run").wants_audit)
        self.assertTrue(classify_intent("审计这个运行").wants_audit)

    def test_wants_calibration_bilingual(self) -> None:
        self.assertTrue(classify_intent("calibrate model").wants_calibration)
        self.assertTrue(classify_intent("校准模型").wants_calibration)


class OpenShapedPromptTests(unittest.TestCase):
    """``is_open_shaped`` covers warm-intro gate behaviour."""

    def test_greetings_are_open_shaped(self) -> None:
        for prompt in ("hi", "Hello there", "你好", "您好"):
            with self.subTest(prompt=prompt):
                self.assertTrue(classify_intent(prompt).is_open_shaped)

    def test_identity_probes_are_open_shaped(self) -> None:
        for prompt in ("what can you do", "who are you", "tell me about yourself"):
            with self.subTest(prompt=prompt):
                self.assertTrue(classify_intent(prompt).is_open_shaped)

    def test_task_verb_prompts_are_not_open_shaped(self) -> None:
        for prompt in (
            "run swmm",
            "build the inp",
            "plot the figure",
            "跑 tecnopolo demo",
        ):
            with self.subTest(prompt=prompt):
                self.assertFalse(classify_intent(prompt).is_open_shaped)

    def test_empty_prompt_is_open_shaped(self) -> None:
        self.assertTrue(classify_intent("").is_open_shaped)
        self.assertTrue(classify_intent("   ").is_open_shaped)


class PlotContinuationTests(unittest.TestCase):
    """``is_plot_continuation`` requires both vocab match AND active_run_dir."""

    def test_plot_continuation_requires_workflow_state_with_active_run(self) -> None:
        # No workflow_state → False even with plot vocab
        signals = classify_intent("plot the figure")
        self.assertFalse(signals.is_plot_continuation)

    def test_plot_continuation_fires_with_active_run_and_vocab(self) -> None:
        signals = classify_intent(
            "plot the figure", workflow_state={"active_run_dir": "/tmp/x"}
        )
        self.assertTrue(signals.is_plot_continuation)

    def test_plot_continuation_false_without_plot_vocab(self) -> None:
        signals = classify_intent(
            "hello", workflow_state={"active_run_dir": "/tmp/x"}
        )
        self.assertFalse(signals.is_plot_continuation)


if __name__ == "__main__":
    unittest.main()
