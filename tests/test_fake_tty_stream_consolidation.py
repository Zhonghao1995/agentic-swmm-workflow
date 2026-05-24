"""Guard against re-introducing per-test ``_FakeTTYStream`` copies.

Issue #190 (#188 follow-up): four test modules defined identical
``class _FakeTTYStream(io.StringIO)`` helpers — only difference being
``isatty()`` returns True. The class is consolidated into
``tests/conftest.py`` so spinner / TTY-rendering tests share one
definition. This file pins the contract:

  1. ``conftest._FakeTTYStream`` exists, subclasses ``io.StringIO``,
     and its ``isatty()`` returns True.
  2. None of the four migrated test modules re-defines ``_FakeTTYStream``
     inline.
"""
from __future__ import annotations

import inspect
import io
import unittest

import conftest


_MIGRATED_MODULES = (
    "tests.test_executor_spinner_wiring",
    "tests.test_tool_spinner_shows_description",
    "tests.test_thinking_spinner_displayed",
    "tests.test_spinner_residue_issue_184",
)


class ConftestExposesFakeTTYStreamTests(unittest.TestCase):
    def test_conftest_has_fake_tty_stream_class(self) -> None:
        self.assertTrue(
            hasattr(conftest, "_FakeTTYStream"),
            "tests/conftest.py must expose _FakeTTYStream for shared use",
        )

    def test_fake_tty_stream_is_a_stringio_subclass(self) -> None:
        self.assertTrue(
            issubclass(conftest._FakeTTYStream, io.StringIO),
            "_FakeTTYStream must subclass io.StringIO so tests can read "
            ".getvalue() like a normal capture buffer",
        )

    def test_fake_tty_stream_claims_to_be_a_tty(self) -> None:
        stream = conftest._FakeTTYStream()
        self.assertTrue(
            stream.isatty(),
            "_FakeTTYStream.isatty() must return True so Spinner picks "
            "the carriage-return TTY rendering path",
        )


class NoInlineFakeTTYStreamCopiesTests(unittest.TestCase):
    def test_migrated_modules_do_not_redefine_fake_tty_stream(self) -> None:
        import importlib

        offenders: list[str] = []
        for dotted in _MIGRATED_MODULES:
            module = importlib.import_module(dotted)
            source = inspect.getsource(module)
            if "class _FakeTTYStream" in source:
                offenders.append(dotted)
        self.assertEqual(
            offenders,
            [],
            "These modules still inline ``class _FakeTTYStream``; "
            "import it from conftest instead: " + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
