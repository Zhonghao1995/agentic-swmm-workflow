"""PRD-08 Phase B (audit #29 + #37).

Two cross-cutting polish items:

* Storm DAT output ships an explicit "values are intensity in mm/h"
  annotation so a modeler reading the SWMM TIMESERIES block knows
  how to interpret the column.
* TUI chrome falls back to ASCII box-drawing when the terminal's
  locale does not advertise UTF-8 (``LANG=C``).
"""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent import tui_chrome
from agentic_swmm.cli import main as cli_main
from tests.conftest import env_overrides as _env_overrides


# ---------------------------------------------------------------------
# Storm intensity annotation (#29)
# ---------------------------------------------------------------------


def _capture(argv: list[str]) -> tuple[str, str, int]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(argv) or 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    return out.getvalue(), err.getvalue(), code


class StormIntensityAnnotationTests(unittest.TestCase):
    """Audit #29: every storm shape's DAT block carries the annotation."""

    def _run_storm(self, *, shape: str, extra: list[str]) -> str:
        with TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "storm.dat"
            argv = [
                "storm",
                "--shape",
                shape,
                "--out",
                str(out_path),
                *extra,
            ]
            _, _, code = _capture(argv)
            self.assertEqual(code, 0)
            self.assertTrue(out_path.is_file())
            return out_path.read_text(encoding="utf-8")

    def test_uniform_storm_dat_carries_intensity_annotation(self) -> None:
        text = self._run_storm(
            shape="uniform",
            extra=["--depth-mm", "25", "--duration-min", "60"],
        )
        self.assertIn("values are intensity in mm/h", text)
        self.assertIn("INTENSITY", text)

    def test_chicago_storm_dat_carries_intensity_annotation(self) -> None:
        text = self._run_storm(
            shape="chicago",
            extra=["--depth-mm", "25", "--duration-min", "60"],
        )
        self.assertIn("values are intensity in mm/h", text)

    def test_huff_storm_dat_carries_intensity_annotation(self) -> None:
        text = self._run_storm(
            shape="huff",
            extra=[
                "--depth-mm",
                "25",
                "--duration-min",
                "60",
                "--quartile",
                "2",
            ],
        )
        self.assertIn("values are intensity in mm/h", text)

    def test_scs_storm_dat_carries_intensity_annotation(self) -> None:
        text = self._run_storm(
            shape="scs",
            extra=["--depth-mm", "25"],
        )
        self.assertIn("values are intensity in mm/h", text)


# ---------------------------------------------------------------------
# TUI chrome ASCII fallback (#37)
# ---------------------------------------------------------------------


# ``_env_overrides`` is the shared ctx-manager from
# ``tests/conftest.py`` (re-exported above as a local alias so the
# existing call sites stay byte-for-byte unchanged).


class UnicodeBoxDrawingFallbackTests(unittest.TestCase):
    def test_utf8_locale_uses_unicode_glyphs(self) -> None:
        with _env_overrides(LANG="en_US.UTF-8", LC_ALL=None, AISWMM_TUI="retro"):
            self.assertTrue(tui_chrome.use_unicode_box_drawing())
            body = tui_chrome.frame("title", ["hi"])
            self.assertIn("╭", body)
            self.assertIn("│", body)
            self.assertIn("╯", body)

    def test_c_locale_falls_back_to_ascii(self) -> None:
        with _env_overrides(LANG="C", LC_ALL=None, AISWMM_TUI="retro"):
            self.assertFalse(tui_chrome.use_unicode_box_drawing())
            body = tui_chrome.frame("title", ["hi"])
            self.assertNotIn("╭", body)
            self.assertNotIn("│", body)
            self.assertIn("+", body)
            self.assertIn("|", body)

    def test_aiswmm_tui_plain_strips_frame_entirely(self) -> None:
        with _env_overrides(LANG="en_US.UTF-8", LC_ALL=None, AISWMM_TUI="plain"):
            body = tui_chrome.frame("[SYS] title", ["hi"])
            # Plain mode strips all box-drawing AND the chrome prefix
            # — falls back to ``== title ==``.
            self.assertIn("== title ==", body)
            self.assertNotIn("╭", body)
            self.assertNotIn("+", body)
            self.assertNotIn("│", body)

    def test_lc_all_c_overrides_lang_utf8(self) -> None:
        # LC_ALL has higher POSIX precedence than LANG.
        with _env_overrides(LANG="en_US.UTF-8", LC_ALL="C", AISWMM_TUI="retro"):
            self.assertFalse(tui_chrome.use_unicode_box_drawing())

    def test_case_insensitive_utf8_match(self) -> None:
        with _env_overrides(LANG="en_US.utf-8", LC_ALL=None, AISWMM_TUI="retro"):
            self.assertTrue(tui_chrome.use_unicode_box_drawing())


if __name__ == "__main__":
    unittest.main()
