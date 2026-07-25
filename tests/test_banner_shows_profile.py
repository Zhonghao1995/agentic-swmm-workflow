"""The startup banner must surface the active permission profile.

Now that QUICK is the default, the user has no other on-screen cue that
the agent is auto-approving read-only tools on their behalf. The
PRD requires the one-line banner to carry a ``profile=quick`` /
``profile=safe`` segment.

The segment must respect ``ui_colors``: dim ANSI on a real tty, plain
text on non-tty / ``NO_COLOR`` (so log scrapers and tests don't see
escape sequences).

These behaviours were originally pinned against
``runtime_loop.format_startup_banner``. That renderer is gone: it was
the second of two startup banners, and the facts it carried moved into
``welcome.render_returning_banner``, which is now the whole first
screen. The assertions follow the behaviour to its new home.
"""
from __future__ import annotations

import re
import unittest
from unittest import mock

from agentic_swmm.agent import ui_colors
from agentic_swmm.agent.welcome import render_returning_banner


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_LAST_SESSION = {"case_name": "demo", "end_utc": "2026-05-14T00:00:00Z"}


def _banner(*, session_label="session-123456", profile_name="quick"):
    return render_returning_banner(
        session_label=session_label,
        profile_name=profile_name,
        run_dir_display="runs/2026-05-14",
        last_session=_LAST_SESSION,
    )


class StartupBannerTests(unittest.TestCase):
    def test_banner_includes_profile_quick(self) -> None:
        with mock.patch.object(ui_colors, "supports_color", return_value=False):
            banner = _banner(profile_name="quick")
        self.assertIn("profile=quick", banner)

    def test_banner_includes_profile_safe(self) -> None:
        with mock.patch.object(ui_colors, "supports_color", return_value=False):
            banner = _banner(profile_name="safe")
        self.assertIn("profile=safe", banner)

    def test_banner_keeps_session_and_date_segments(self) -> None:
        # The merge must not lose facts. Session label, run directory and
        # the slash-command hints all survived from the two former
        # banners into the one that remains.
        with mock.patch.object(ui_colors, "supports_color", return_value=False):
            banner = _banner(session_label="session-654321")
        self.assertIn("AISWMM v", banner)
        self.assertIn("session-654321", banner)
        self.assertIn("runs/2026-05-14", banner)
        self.assertIn("/exit", banner)
        self.assertIn("/new-session", banner)

    def test_non_tty_fallback_strips_ansi(self) -> None:
        # supports_color() returns False on non-tty / NO_COLOR — the
        # banner must come back with zero ANSI escapes. This is what
        # CI log scrapers and tests will see.
        with mock.patch.object(ui_colors, "supports_color", return_value=False):
            banner = _banner(session_label="session-000000")
        self.assertEqual(banner, _ANSI_RE.sub("", banner))
        self.assertNotIn("\x1b[", banner)

    def test_tty_dims_profile_segment(self) -> None:
        # On a real tty the profile segment is wrapped in DIM so it
        # doesn't dominate the line. The exact placement is internal;
        # all we assert is that DIM appears around the segment.
        with mock.patch.object(ui_colors, "supports_color", return_value=True):
            banner = _banner(session_label="session-000000")
        self.assertIn(ui_colors.DIM, banner)
        self.assertIn("profile=quick", banner)
        self.assertIn(ui_colors.RESET, banner)


if __name__ == "__main__":
    unittest.main()
