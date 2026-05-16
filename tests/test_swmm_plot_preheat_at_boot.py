"""Cold-start preheat contract for the swmm-plot MCP server (issue #109).

The first ``plot_rain_runoff_si`` tool call against a freshly-spawned
swmm-plot MCP server used to block ~89 seconds inside a user-visible
``you>`` turn. The cost was matplotlib's font cache rebuild and
swmmtoolbox's first import. Both caches persist to disk, so call #2
is fast — the user just paid the tax once, locked inside a tool call,
with no feedback.

The fix is two-part:

  1. ``mcp/swmm-plot/server.js`` fires a non-blocking preheat subprocess
     at boot that imports matplotlib + swmmtoolbox and writes the font
     cache to disk. The MCP ``initialize`` handshake must not wait for
     it (preheat MUST NOT touch the server's stdout — that channel
     carries JSON-RPC framing).

  2. ``skills/swmm-plot/scripts/plot_rain_runoff_si.py`` prints a
     one-line stderr hint when matplotlib's font cache is missing, so
     the user knows what's happening if the preheat hasn't finished
     yet (or was skipped because Python/deps were unavailable).

This module guards both halves structurally. A proper end-to-end test
would have to clear ``~/.matplotlib/`` and spawn the MCP server fresh,
which is too disruptive for CI (it would slow other matplotlib tests
on the same runner and leak state). The structural guards are cheap,
fail loudly if the preheat is ripped out, and follow the same pattern
as ``WarmIntroFiresOncePerSessionRegression`` in
``test_self_intro_on_open_prompt.py``.
"""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_JS = REPO_ROOT / "mcp" / "swmm-plot" / "server.js"
PLOT_PY = REPO_ROOT / "skills" / "swmm-plot" / "scripts" / "plot_rain_runoff_si.py"


class ServerPreheatsAtBootTests(unittest.TestCase):
    """``mcp/swmm-plot/server.js`` fires a preheat spawn at startup."""

    def setUp(self) -> None:
        self.source = SERVER_JS.read_text()

    def test_server_js_exists(self) -> None:
        # If this fails the swmm-plot MCP server has moved — the rest
        # of the guards in this module need their paths updated too.
        self.assertTrue(
            SERVER_JS.is_file(),
            f"swmm-plot server entrypoint not found at {SERVER_JS}",
        )

    def test_preheat_function_defined(self) -> None:
        self.assertIn(
            "function preheatPlotEnv(",
            self.source,
            "preheatPlotEnv() must be defined in mcp/swmm-plot/server.js. "
            "Without it, the first plot tool call after MCP boot pays the "
            "~89s matplotlib/swmmtoolbox cold-start tax inside a user-"
            "visible `you>` turn (issue #109).",
        )

    def test_preheat_invoked_before_transport_connect(self) -> None:
        # Order matters: the preheat must be kicked off before
        # ``server.connect(transport)`` blocks on the JSON-RPC loop,
        # otherwise the warm-up never starts.
        preheat_idx = self.source.find("preheatPlotEnv()")
        connect_idx = self.source.find("server.connect(transport)")
        self.assertGreater(
            preheat_idx,
            -1,
            "preheatPlotEnv() is never invoked in server.js — "
            "defining the function is not enough.",
        )
        self.assertGreater(
            connect_idx,
            -1,
            "could not locate `server.connect(transport)` in server.js — "
            "refactor likely renamed the wiring; update this guard.",
        )
        self.assertLess(
            preheat_idx,
            connect_idx,
            "preheatPlotEnv() must be called before server.connect(transport) "
            "so warm-up runs in parallel with the JSON-RPC handshake.",
        )

    def test_preheat_imports_matplotlib_and_swmmtoolbox(self) -> None:
        # Both are needed: matplotlib for the font cache write, and
        # swmmtoolbox to materialise its first-import __pycache__.
        self.assertIn(
            "import matplotlib",
            self.source,
            "preheat must import matplotlib so plt.figure() writes the font cache",
        )
        self.assertIn(
            "plt.figure()",
            self.source,
            "preheat must call plt.figure() — that's what triggers font discovery",
        )
        self.assertIn(
            "import swmmtoolbox",
            self.source,
            "preheat must import swmmtoolbox so its __pycache__ exists "
            "before the first tool call",
        )

    def test_preheat_does_not_await_or_block_handshake(self) -> None:
        # The preheat MUST be fire-and-forget. If we ever change it to
        # ``await preheatPlotEnv()`` the JSON-RPC initialize handshake
        # will block on the very thing we're trying to hide from the
        # user — the cure becomes the disease.
        self.assertNotIn(
            "await preheatPlotEnv()",
            self.source,
            "preheat must be fire-and-forget — awaiting it blocks the "
            "MCP initialize handshake, defeating the whole point.",
        )


class PlotScriptColdStartWarningTests(unittest.TestCase):
    """``plot_rain_runoff_si.py`` warns on stderr when the font cache is missing."""

    def setUp(self) -> None:
        self.source = PLOT_PY.read_text()

    def test_plot_script_exists(self) -> None:
        self.assertTrue(
            PLOT_PY.is_file(),
            f"plot_rain_runoff_si.py not found at {PLOT_PY}",
        )

    def test_cold_start_warning_helper_present(self) -> None:
        self.assertIn(
            "_warn_if_cold_start",
            self.source,
            "_warn_if_cold_start() must exist so the user gets a hint "
            "when the MCP preheat hasn't (or couldn't) warm the cache.",
        )

    def test_warning_runs_before_matplotlib_import(self) -> None:
        # The whole point of the warning is that it fires *before* the
        # ~30-90s import latency, not after.
        helper_call_idx = self.source.find("_warn_if_cold_start()")
        # Match the first non-conditional matplotlib import — i.e. the
        # one at module top level, not the one inside the helper.
        # We pick the ``matplotlib.use('Agg')`` line as the anchor
        # because it's only present at top level.
        mpl_use_idx = self.source.find("matplotlib.use('Agg')")
        self.assertGreater(
            helper_call_idx,
            -1,
            "_warn_if_cold_start() must be called at module load time, "
            "before the slow matplotlib import.",
        )
        self.assertGreater(
            mpl_use_idx,
            -1,
            "could not locate matplotlib.use('Agg') in plot script — "
            "refactor likely changed the backend hook; update this guard.",
        )
        self.assertLess(
            helper_call_idx,
            mpl_use_idx,
            "_warn_if_cold_start() must run BEFORE the matplotlib import. "
            "Calling it after defeats the point — the user has already "
            "waited the 30-90s before the warning prints.",
        )

    def test_warning_writes_to_stderr_not_stdout(self) -> None:
        # The MCP server captures stdout for the JSON success payload.
        # If we ever write the warning to stdout we'd corrupt that
        # payload. Belt-and-braces guard.
        self.assertIn(
            "sys.stderr.write",
            self.source,
            "cold-start warning must use sys.stderr — stdout is reserved "
            "for the MCP tool's JSON result payload.",
        )


if __name__ == "__main__":
    unittest.main()
