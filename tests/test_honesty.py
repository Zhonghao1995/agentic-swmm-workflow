"""Tests for ``agentic_swmm.agent.honesty`` (PRD-08 Phase A.1, cluster 1).

The honesty layer is the runtime substrate behind the trust-restoring
fixes in the UX polish layer. These tests pin the wire format of every
public helper plus the env-var opt-out path.
"""

from __future__ import annotations

import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.honesty import (
    HONESTY_DISABLE_ENV,
    STUB_BANNER,
    SwmmRunError,
    assert_swmm_run_ok,
    emit_silent_default_warning,
    emit_silent_override_warning,
    fail_fast_if_path_missing,
    is_honesty_layer_disabled,
    scan_rpt_for_errors,
)


# ---------------------------------------------------------------------------
# scan_rpt_for_errors
# ---------------------------------------------------------------------------


class ScanRptForErrorsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_returns_error_lines_verbatim(self) -> None:
        rpt = self.tmp / "model.rpt"
        rpt.write_text(
            "  EPA SWMM 5.2\n\n  ERROR 205: invalid keyword at line 5\n",
            encoding="utf-8",
        )
        self.assertEqual(
            scan_rpt_for_errors(rpt),
            ["ERROR 205: invalid keyword at line 5"],
        )

    def test_clean_rpt_returns_empty(self) -> None:
        rpt = self.tmp / "model.rpt"
        rpt.write_text(
            "  EPA SWMM 5.2\n  Continuity Error (%) .....   -0.05\n",
            encoding="utf-8",
        )
        self.assertEqual(scan_rpt_for_errors(rpt), [])

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(scan_rpt_for_errors(self.tmp / "nope.rpt"), [])

    def test_multiple_errors_returned_in_order(self) -> None:
        rpt = self.tmp / "model.rpt"
        rpt.write_text(
            "ERROR 100: first\n"
            "ERROR 200: second\n"
            "narrative line\n"
            "ERROR 300: third\n",
            encoding="utf-8",
        )
        self.assertEqual(
            scan_rpt_for_errors(rpt),
            ["ERROR 100: first", "ERROR 200: second", "ERROR 300: third"],
        )

    def test_narrative_use_of_word_error_does_not_match(self) -> None:
        rpt = self.tmp / "model.rpt"
        rpt.write_text(
            "  Continuity Error (%) .....  -0.05\n"
            "The error in mass balance was small.\n",
            encoding="utf-8",
        )
        self.assertEqual(scan_rpt_for_errors(rpt), [])


# ---------------------------------------------------------------------------
# assert_swmm_run_ok + opt-out
# ---------------------------------------------------------------------------


class AssertSwmmRunOkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # Ensure the opt-out env var is not leaking into the test from
        # the caller's environment.
        self._saved_env = os.environ.pop(HONESTY_DISABLE_ENV, None)

    def tearDown(self) -> None:
        os.environ.pop(HONESTY_DISABLE_ENV, None)
        if self._saved_env is not None:
            os.environ[HONESTY_DISABLE_ENV] = self._saved_env

    def test_raises_on_error_lines(self) -> None:
        rpt = self.tmp / "model.rpt"
        rpt.write_text("ERROR 205: invalid keyword at line 5\n", encoding="utf-8")
        with self.assertRaises(SwmmRunError) as cm:
            assert_swmm_run_ok(rpt)
        self.assertEqual(
            cm.exception.error_lines,
            ["ERROR 205: invalid keyword at line 5"],
        )
        self.assertEqual(cm.exception.rpt_path, rpt)

    def test_returns_none_on_clean_report(self) -> None:
        rpt = self.tmp / "model.rpt"
        rpt.write_text("clean run\n", encoding="utf-8")
        self.assertIsNone(assert_swmm_run_ok(rpt))

    def test_opt_out_disables_raise(self) -> None:
        rpt = self.tmp / "model.rpt"
        rpt.write_text("ERROR 999: bad\n", encoding="utf-8")
        os.environ[HONESTY_DISABLE_ENV] = "1"
        try:
            self.assertIsNone(assert_swmm_run_ok(rpt))
        finally:
            os.environ.pop(HONESTY_DISABLE_ENV, None)

    def test_opt_out_falsey_value_keeps_layer_active(self) -> None:
        rpt = self.tmp / "model.rpt"
        rpt.write_text("ERROR 999: bad\n", encoding="utf-8")
        os.environ[HONESTY_DISABLE_ENV] = "0"
        try:
            with self.assertRaises(SwmmRunError):
                assert_swmm_run_ok(rpt)
        finally:
            os.environ.pop(HONESTY_DISABLE_ENV, None)

    def test_is_honesty_layer_disabled_reports_state(self) -> None:
        self.assertFalse(is_honesty_layer_disabled())
        os.environ[HONESTY_DISABLE_ENV] = "1"
        try:
            self.assertTrue(is_honesty_layer_disabled())
        finally:
            os.environ.pop(HONESTY_DISABLE_ENV, None)
        os.environ[HONESTY_DISABLE_ENV] = "0"
        try:
            self.assertFalse(is_honesty_layer_disabled())
        finally:
            os.environ.pop(HONESTY_DISABLE_ENV, None)


# ---------------------------------------------------------------------------
# emit_silent_override_warning / emit_silent_default_warning
# ---------------------------------------------------------------------------


class WarningEmitterTests(unittest.TestCase):
    def test_override_warning_wire_format(self) -> None:
        stream = io.StringIO()
        emit_silent_override_warning(
            stream,
            flag_user_set="--depth-mm",
            flag_user_value=25,
            reason="--idf is set; computed depth from IDF is 72.19 mm",
        )
        self.assertEqual(
            stream.getvalue(),
            "warning: --depth-mm 25 ignored because --idf is set; "
            "computed depth from IDF is 72.19 mm\n",
        )

    def test_default_warning_wire_format(self) -> None:
        stream = io.StringIO()
        emit_silent_default_warning(
            stream,
            flag_omitted="--shape",
            default_chosen="uniform",
            hint="pass --shape chicago for IDF-driven hyetograph",
        )
        self.assertEqual(
            stream.getvalue(),
            "note: --shape not supplied, using uniform; "
            "pass --shape chicago for IDF-driven hyetograph\n",
        )

    def test_override_warning_single_line_no_embedded_newline(self) -> None:
        stream = io.StringIO()
        emit_silent_override_warning(
            stream,
            flag_user_set="--depth-mm",
            flag_user_value=25,
            reason="reason",
        )
        body = stream.getvalue()
        # Exactly one trailing newline; no embedded newlines so log
        # scrapers can grep per-line.
        self.assertEqual(body.count("\n"), 1)


# ---------------------------------------------------------------------------
# fail_fast_if_path_missing
# ---------------------------------------------------------------------------


class FailFastTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_existing_path_returns_none(self) -> None:
        target = self.tmp / "exists.txt"
        target.write_text("x", encoding="utf-8")
        self.assertIsNone(fail_fast_if_path_missing(target, "--target"))

    def test_missing_path_raises_systemexit_2_with_stderr(self) -> None:
        stream = io.StringIO()
        target = self.tmp / "nope.txt"
        with self.assertRaises(SystemExit) as cm:
            fail_fast_if_path_missing(target, "--target", stream=stream)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("error: --target does not exist:", stream.getvalue())
        self.assertIn("nope.txt", stream.getvalue())

    def test_directory_paths_also_pass(self) -> None:
        # ``Path.exists`` is True for directories too — this verb is
        # used for both file flags (--inp) and directory flags (--run-a).
        self.assertIsNone(
            fail_fast_if_path_missing(self.tmp, "--dir")
        )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class ConstantsTests(unittest.TestCase):
    def test_stub_banner_contains_the_three_required_substrings(self) -> None:
        # Each line is load-bearing for the UX audit fix; keep them
        # pinned so a future tweak does not silently weaken the
        # disclosure.
        self.assertIn("synthetic walker", STUB_BANNER)
        self.assertIn("solver hookup is pending", STUB_BANNER)
        self.assertIn("not a calibration", STUB_BANNER)

    def test_disable_env_name_is_documented(self) -> None:
        self.assertEqual(HONESTY_DISABLE_ENV, "AISWMM_DISABLE_HONESTY_LAYER")


if __name__ == "__main__":
    unittest.main()
