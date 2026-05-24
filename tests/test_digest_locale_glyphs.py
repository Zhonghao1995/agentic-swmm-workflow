"""Issue #193 item 3: digest glyphs respect ``tui_chrome.use_unicode_box_drawing()``.

``agentic_swmm/agent/tui_chrome.py`` is the project's source of truth
for "should we render Unicode box-drawing / fancy glyphs?" — it
returns ``False`` on ``LC_ALL=C`` / non-UTF-8 locales and on
``AISWMM_TUI=plain``. Every other chrome surface routes through it.

The digest renderer was added without that gate, so a user on a
non-UTF-8 tty saw ``???`` / mojibake every step row. These tests pin
the ASCII fallback:

* ``✓`` -> ``v``
* ``✗`` -> ``x``
* ``─`` -> ``-``

Tests use ``monkeypatch`` on the env vars the locale-detection helper
reads (``LC_ALL`` / ``AISWMM_TUI``) so the behaviour is exercised
without touching the OS-level locale.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.digest_render import render_final_summary, render_step
from tests.conftest import env_overrides


class StepGlyphLocaleTests(unittest.TestCase):
    def test_unicode_locale_keeps_check_and_cross_glyphs(self) -> None:
        # Sanity: a UTF-8 locale renders the existing Unicode glyphs
        # so the default behaviour is byte-for-byte unchanged.
        with env_overrides(LC_ALL="en_US.UTF-8", AISWMM_TUI="retro"):
            row = render_step(
                step=1,
                tool="list_dir",
                is_read_only=True,
                prompted=False,
                approved=True,
                ok=True,
                brief="3 entries",
                error_detail=None,
            )
        self.assertIn("✓", row)
        self.assertNotIn(" v ", row)

    def test_non_utf8_locale_falls_back_to_ascii_check_mark(self) -> None:
        # LC_ALL=C is the canonical non-UTF-8 signal; tui_chrome.use_unicode_box_drawing()
        # returns False so the renderer must swap ✓ -> v.
        with env_overrides(LC_ALL="C", LANG=None, AISWMM_TUI="retro"):
            row = render_step(
                step=1,
                tool="list_dir",
                is_read_only=True,
                prompted=False,
                approved=True,
                ok=True,
                brief="3 entries",
                error_detail=None,
            )
        self.assertNotIn("✓", row)
        self.assertIn(" v ", row)

    def test_non_utf8_locale_falls_back_to_ascii_cross_mark(self) -> None:
        with env_overrides(LC_ALL="C", LANG=None, AISWMM_TUI="retro"):
            row = render_step(
                step=2,
                tool="list_dir",
                is_read_only=True,
                prompted=False,
                approved=True,
                ok=False,
                brief="path missing",
                error_detail=None,
            )
        self.assertNotIn("✗", row)
        self.assertIn(" x ", row)

    def test_plain_mode_also_falls_back_to_ascii(self) -> None:
        # AISWMM_TUI=plain forces ASCII even with a UTF-8 locale.
        with env_overrides(LC_ALL="en_US.UTF-8", AISWMM_TUI="plain"):
            row = render_step(
                step=3,
                tool="list_dir",
                is_read_only=True,
                prompted=False,
                approved=True,
                ok=True,
                brief="ok",
                error_detail=None,
            )
        self.assertNotIn("✓", row)
        self.assertIn(" v ", row)


class FinalSummarySeparatorLocaleTests(unittest.TestCase):
    def test_unicode_locale_uses_dash_em_separator(self) -> None:
        with env_overrides(LC_ALL="en_US.UTF-8", AISWMM_TUI="retro"):
            with TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "run"
                run_dir.mkdir()
                (run_dir / "manifest.json").write_text(
                    json.dumps({"metrics": {}}), encoding="utf-8"
                )
                block = render_final_summary([run_dir])
        # 25 box-drawing dashes — the existing Unicode separator.
        self.assertIn("─" * 25, block)

    def test_non_utf8_locale_uses_ascii_hyphen_separator(self) -> None:
        with env_overrides(LC_ALL="C", LANG=None, AISWMM_TUI="retro"):
            with TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "run"
                run_dir.mkdir()
                (run_dir / "manifest.json").write_text(
                    json.dumps({"metrics": {}}), encoding="utf-8"
                )
                block = render_final_summary([run_dir])
        self.assertNotIn("─" * 25, block)
        # 25 ASCII hyphens form the fallback separator.
        self.assertIn("-" * 25, block)


if __name__ == "__main__":
    unittest.main()
