"""``--ignore-memory`` top-level CLI flag.

The flag is an escape hatch that sets
``AISWMM_DISABLE_MEMORY_INFORMED=1`` for the duration of one
invocation. It must not pollute the environment of subsequent calls
in the same process, and it must work regardless of where on the
command line the user puts it.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from agentic_swmm.cli import _strip_ignore_memory


class StripIgnoreMemoryTests(unittest.TestCase):
    def test_flag_absent_returns_unchanged(self) -> None:
        argv = ["agent", "run", "--inp", "model.inp"]
        cleaned, present = _strip_ignore_memory(argv)
        self.assertEqual(cleaned, argv)
        self.assertFalse(present)

    def test_flag_at_front_is_stripped(self) -> None:
        cleaned, present = _strip_ignore_memory(
            ["--ignore-memory", "agent", "run"]
        )
        self.assertEqual(cleaned, ["agent", "run"])
        self.assertTrue(present)

    def test_flag_at_end_is_stripped(self) -> None:
        cleaned, present = _strip_ignore_memory(
            ["agent", "run", "--ignore-memory"]
        )
        self.assertEqual(cleaned, ["agent", "run"])
        self.assertTrue(present)

    def test_flag_in_middle_is_stripped(self) -> None:
        cleaned, present = _strip_ignore_memory(
            ["plot", "--ignore-memory", "--run-dir", "x"]
        )
        self.assertEqual(cleaned, ["plot", "--run-dir", "x"])
        self.assertTrue(present)


class MainIgnoreMemoryEnvLifecycleTests(unittest.TestCase):
    """Setting --ignore-memory must not leak across calls."""

    def test_env_restored_after_main_when_flag_set(self) -> None:
        from agentic_swmm.agent.feature_flags import MEMORY_INFORMED_ENV
        from agentic_swmm.cli import main

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(MEMORY_INFORMED_ENV, None)
            with mock.patch("sys.argv", ["aiswmm", "--ignore-memory", "--version"]):
                with self.assertRaises(SystemExit):
                    main()
            # After main returns, the env var must be unset again.
            self.assertNotIn(MEMORY_INFORMED_ENV, os.environ)

    def test_pre_existing_env_value_restored(self) -> None:
        from agentic_swmm.agent.feature_flags import MEMORY_INFORMED_ENV
        from agentic_swmm.cli import main

        with mock.patch.dict(
            os.environ, {MEMORY_INFORMED_ENV: "yes"}, clear=False
        ):
            with mock.patch("sys.argv", ["aiswmm", "--ignore-memory", "--version"]):
                with self.assertRaises(SystemExit):
                    main()
            # The pre-existing value must be restored after the call.
            self.assertEqual(os.environ.get(MEMORY_INFORMED_ENV), "yes")


if __name__ == "__main__":
    unittest.main()
