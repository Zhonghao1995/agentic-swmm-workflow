"""PRD-TUI-REDESIGN: runtime-loop tool-execution banners.

``execute_with_chrome`` wraps a single ``executor.execute(call)`` call
with ``[SYS] EXECUTING <tool>`` before and ``[INF] COMPLETE`` /
``[ERR] FAILED`` after. Both retro and plain modes are exercised.
"""

from __future__ import annotations

import io
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from agentic_swmm.agent import runtime_loop
from agentic_swmm.agent import tui_chrome


class _StubExecutor:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def execute(self, call, *, index=None):
        self.calls.append((call, index))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class RuntimeLoopToolBannerTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["AISWMM_TUI"] = "retro"
        os.environ.pop("NO_COLOR", None)

    def _call(self):
        return SimpleNamespace(name="swmm_run", args={"inp": "demo.inp"})

    def test_success_banners(self) -> None:
        buf = io.StringIO()
        executor = _StubExecutor({"tool": "swmm_run", "ok": True, "summary": "ran"})
        runtime_loop.execute_with_chrome(executor, self._call(), index=1, stream=buf)
        out = buf.getvalue()
        # Strip ANSI for the substring checks.
        import re

        plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
        self.assertIn("[SYS] EXECUTING swmm_run", plain)
        self.assertIn("[INF] COMPLETE  swmm_run", plain)
        # Both banners exist; the [SYS] line precedes the [INF] line.
        self.assertLess(plain.index("[SYS]"), plain.index("[INF]"))

    def test_failed_result_emits_err_banner(self) -> None:
        buf = io.StringIO()
        executor = _StubExecutor(
            {"tool": "swmm_run", "ok": False, "summary": "binary missing"}
        )
        runtime_loop.execute_with_chrome(executor, self._call(), stream=buf)
        plain = _strip_ansi(buf.getvalue())
        self.assertIn("[SYS] EXECUTING swmm_run", plain)
        self.assertIn("[ERR] FAILED    swmm_run", plain)
        self.assertNotIn("[INF] COMPLETE", plain)

    def test_raised_exception_emits_err_banner_and_reraises(self) -> None:
        buf = io.StringIO()
        executor = _StubExecutor(RuntimeError("kaboom"))
        with self.assertRaises(RuntimeError):
            runtime_loop.execute_with_chrome(executor, self._call(), stream=buf)
        plain = _strip_ansi(buf.getvalue())
        self.assertIn("[SYS] EXECUTING swmm_run", plain)
        self.assertIn("[ERR] FAILED    swmm_run", plain)

    def test_plain_mode_strips_prefixes_but_keeps_timing(self) -> None:
        os.environ["AISWMM_TUI"] = "plain"
        buf = io.StringIO()
        executor = _StubExecutor({"tool": "swmm_run", "ok": True})
        runtime_loop.execute_with_chrome(executor, self._call(), stream=buf)
        out = buf.getvalue()
        self.assertNotIn("[SYS]", out)
        self.assertNotIn("[INF]", out)
        self.assertNotIn("\x1b[", out)
        # Tool name and timing still emitted (the user wants the
        # scrollback to be readable).
        self.assertIn("EXECUTING swmm_run", out)
        self.assertIn("COMPLETE  swmm_run", out)


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


if __name__ == "__main__":
    unittest.main()
