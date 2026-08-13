"""SWMM's external-file error names the series, never the file.

From a live session:

    ✗ MCP transport failed: swmm_run failed: ERROR 361: could not open
      external file used for Time Series TEMP_ROME.

The planner had no way to name the file it needed. TEMP_ROME is a series; the
filename it binds to sits in the INP's [TIMESERIES] section, and the INP had
been copied into a new folder without its .dat files. Working that out took a
repository-wide search and two and a half minutes.
"""
from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.error_remediation import swmm_external_file_error

MESSAGE = "swmm_run failed: ERROR 361: could not open external file used for Time Series TEMP_ROME."
INP_BODY = """[TIMESERIES]
;;Name           Date       Time       Value
TEMP_ROME        FILE "EXT_TEM_199401_ROME_NASA_mm_day.dat"
EVP_ROME         FILE "EXT_EVP_199401_ROME_NASA_mm_day.dat"
"""


class ExternalFileRemediationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.case = self.root / "examples" / "33"
        self.case.mkdir(parents=True)
        self.inp = self.case / "22.inp"
        self.inp.write_text(INP_BODY, encoding="utf-8")

    def test_unrelated_messages_pass_through(self) -> None:
        # Callers hand every failure to the builder, so a miss must be a
        # no-op rather than a guess.
        self.assertIsNone(swmm_external_file_error("npm ci failed", inp_path=self.inp))

    def test_names_the_file_the_series_binds_to(self) -> None:
        err = swmm_external_file_error(MESSAGE, inp_path=self.inp)
        self.assertIsNotNone(err)
        self.assertIn("EXT_TEM_199401_ROME_NASA_mm_day.dat", err.cause)
        self.assertIn(str(self.case), err.cause)

    def test_trailing_period_is_not_part_of_the_series_name(self) -> None:
        # SWMM ends the line with a period; a greedy capture makes the name
        # "TEMP_ROME." which then matches nothing in [TIMESERIES].
        err = swmm_external_file_error(MESSAGE, inp_path=self.inp)
        self.assertIn("'TEMP_ROME'", err.cause)

    def test_points_at_a_copy_elsewhere_in_the_project(self) -> None:
        donor = self.root / "examples" / "tecnopolo"
        donor.mkdir(parents=True)
        (donor / "EXT_TEM_199401_ROME_NASA_mm_day.dat").write_text("1\n", encoding="utf-8")
        err = swmm_external_file_error(MESSAGE, inp_path=self.inp, search_root=self.root)
        self.assertIn("copy it next to 22.inp", err.hint)
        self.assertIn("tecnopolo", err.hint)

    def test_with_no_copy_anywhere_it_still_says_what_to_do(self) -> None:
        err = swmm_external_file_error(MESSAGE, inp_path=self.inp, search_root=self.root)
        self.assertIn("copy EXT_TEM_199401_ROME_NASA_mm_day.dat", err.hint)
        self.assertIn("relative", err.hint)

    def test_a_present_file_is_diagnosed_differently(self) -> None:
        (self.case / "EXT_TEM_199401_ROME_NASA_mm_day.dat").write_text("1\n", encoding="utf-8")
        err = swmm_external_file_error(MESSAGE, inp_path=self.inp, search_root=self.root)
        self.assertIn("present but could not be opened", err.cause)
        self.assertIn("permissions", err.hint)

    def test_without_an_inp_it_still_explains_the_relative_path_rule(self) -> None:
        err = swmm_external_file_error(MESSAGE)
        self.assertIsNotNone(err)
        self.assertIn("relative", err.hint)

    def test_the_real_example_model_resolves(self) -> None:
        # Guards the [TIMESERIES] parse against the actual shipped syntax.
        repo_inp = Path("examples/tecnopolo/tecnopolo_r1_199401.inp")
        if not repo_inp.exists():  # pragma: no cover - repo layout guard
            self.skipTest("example model not present")
        copied = self.case / "copied.inp"
        shutil.copy(repo_inp, copied)
        err = swmm_external_file_error(MESSAGE, inp_path=copied, search_root=Path("examples"))
        self.assertIn("EXT_TEM_199401_ROME_NASA_mm_day.dat", err.cause)


class TransportSummaryTests(unittest.TestCase):
    def test_the_mcp_failure_summary_carries_the_hint(self) -> None:
        from agentic_swmm.agent.tool_handlers._shared import _augment_engine_failure
        from agentic_swmm.agent.types import ToolCall

        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "22.inp"
            inp.write_text(INP_BODY, encoding="utf-8")
            call = ToolCall(name="run_swmm_inp", args={"inp_path": str(inp)})
            summary = _augment_engine_failure(call, MESSAGE)
        self.assertIn("ERROR 361", summary, "the original engine line must survive")
        self.assertIn("EXT_TEM_199401_ROME_NASA_mm_day.dat", summary)

    def test_an_unrelated_failure_is_untouched(self) -> None:
        from agentic_swmm.agent.tool_handlers._shared import _augment_engine_failure
        from agentic_swmm.agent.types import ToolCall

        call = ToolCall(name="run_swmm_inp", args={})
        self.assertEqual(_augment_engine_failure(call, "MCP transport failed: spawn ENOEXEC"),
                         "MCP transport failed: spawn ENOEXEC")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
