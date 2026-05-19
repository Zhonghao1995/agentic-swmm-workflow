"""Tests for the runtime feature-flag helpers.

The two flags surface env-var-controlled opt-outs for the SWMM
pre/postflight gates and the memory-informed dispatch path. Both
defaults must remain "enabled" so a user who has set neither variable
gets the full memory-informed runtime — the opt-outs are escape
hatches, not the default UX.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from agentic_swmm.agent import feature_flags


class SwmmGatesDisabledTests(unittest.TestCase):
    def test_unset_env_returns_false(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(feature_flags.SWMM_GATES_ENV, None)
            self.assertFalse(feature_flags.swmm_gates_disabled())

    def test_one_returns_true(self) -> None:
        with mock.patch.dict(os.environ, {feature_flags.SWMM_GATES_ENV: "1"}):
            self.assertTrue(feature_flags.swmm_gates_disabled())

    def test_true_case_insensitive(self) -> None:
        for value in ("true", "TRUE", "True", "yes", "YES", "on", "ON"):
            with self.subTest(value=value):
                with mock.patch.dict(
                    os.environ, {feature_flags.SWMM_GATES_ENV: value}
                ):
                    self.assertTrue(feature_flags.swmm_gates_disabled())

    def test_zero_returns_false(self) -> None:
        with mock.patch.dict(os.environ, {feature_flags.SWMM_GATES_ENV: "0"}):
            self.assertFalse(feature_flags.swmm_gates_disabled())

    def test_empty_string_returns_false(self) -> None:
        with mock.patch.dict(os.environ, {feature_flags.SWMM_GATES_ENV: ""}):
            self.assertFalse(feature_flags.swmm_gates_disabled())

    def test_random_string_returns_false(self) -> None:
        with mock.patch.dict(
            os.environ, {feature_flags.SWMM_GATES_ENV: "maybe"}
        ):
            self.assertFalse(feature_flags.swmm_gates_disabled())


class MemoryInformedDisabledTests(unittest.TestCase):
    def test_unset_env_returns_false(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(feature_flags.MEMORY_INFORMED_ENV, None)
            self.assertFalse(feature_flags.memory_informed_disabled())

    def test_one_returns_true(self) -> None:
        with mock.patch.dict(
            os.environ, {feature_flags.MEMORY_INFORMED_ENV: "1"}
        ):
            self.assertTrue(feature_flags.memory_informed_disabled())

    def test_flags_are_independent(self) -> None:
        """Setting SWMM_GATES does not affect MEMORY_INFORMED and vice versa."""
        with mock.patch.dict(
            os.environ, {feature_flags.SWMM_GATES_ENV: "1"}
        ):
            os.environ.pop(feature_flags.MEMORY_INFORMED_ENV, None)
            self.assertTrue(feature_flags.swmm_gates_disabled())
            self.assertFalse(feature_flags.memory_informed_disabled())
        with mock.patch.dict(
            os.environ, {feature_flags.MEMORY_INFORMED_ENV: "1"}
        ):
            os.environ.pop(feature_flags.SWMM_GATES_ENV, None)
            self.assertFalse(feature_flags.swmm_gates_disabled())
            self.assertTrue(feature_flags.memory_informed_disabled())


if __name__ == "__main__":
    unittest.main()
